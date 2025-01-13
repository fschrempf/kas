# kas - setup tool for bitbake based projects
#
# Copyright (c) Siemens AG, 2017-2024
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
    This plugin implements the ``kas lock`` command.

    When this command is executed a locking spec is created which only contains
    the exact commit of each repository. This is used to pin the commit of
    floating branches and tags, while still keeping an easy update path. The
    lockfile is created next to the first file on the kas cmdline. For details
    on the locking support, see :class:`kas.includehandler.IncludeHandler`.

    Please note:

    - all referenced repositories are checked out to resolve cross-repo configs
    - all branches are resolved before patches are applied

    Example (call again to regenerate lockfile).
    The lockfile is created as ``kas-project.lock.yml``::

        kas lock --update kas-project.yml

    The generated lockfile will automatically be used to pin the revisions::

        kas build kas-project.yml

    Note, that the lockfiles should be checked-in into the VCS.
"""

import logging
import os
from pathlib import Path
from kas.context import get_context
from kas.includehandler import ConfigFile
from kas.plugins.checkout import Checkout
from kas.plugins.dump import Dump, IoTarget

__license__ = 'MIT'
__copyright__ = 'Copyright (c) Siemens AG, 2024'


class Lock(Checkout):
    """
    Implements a kas plugin to create and update kas project lockfiles.
    """

    name = 'lock'
    helpmsg = (
        'Create and update kas project lockfiles.'
    )

    @classmethod
    def setup_parser(cls, parser):
        super().setup_parser(parser)
        Dump.setup_parser_format_args(parser)

    @staticmethod
    def _path_is_relative_to(path, prefix):
        """
        Path.is_relative_to implementation for python < 3.9
        """
        try:
            path.relative_to(prefix)
            return True
        except ValueError:
            return False

    def _is_external_lockfile(self, lockfile):
        for (_, r) in self.repos:
            if self._path_is_relative_to(Path(lockfile.filename), r.path):
                # repos managed by kas (ops=enabled) are external
                if not r.operations_disabled:
                    return True
        return False

    def _update_lockfile(self, lockfile, repos_to_lock, args):
        """
        Update all locks in the given lockfile. No new locks are added.
        """
        output = IoTarget(target=lockfile, managed=True)
        lockfile_config = lockfile.config
        changed = False

        for k, v in lockfile_config['overrides']['repos'].items():
            for rk, r in repos_to_lock:
                if k == rk:
                    repos_to_lock.remove((rk, r))
                    if v['commit'] == r.revision:
                        logging.info('Lock of %s is up-to-date: %s',
                                     r.name, r.revision)
                    elif not self._is_external_lockfile(lockfile):
                        logging.info('Updating lock of %s: %s -> %s',
                                     r.name, v['commit'], r.revision)
                        v['commit'] = r.revision
                        changed = True
                    else:
                        logging.warning('Repo %s is locked in remote lockfile %s. '
                                        'Not updating.',
                                        r.name, lockfile.filename)
                        continue

        if not changed:
            return repos_to_lock

        logging.info('Updating lockfile %s',
                     os.path.relpath(lockfile.filename, os.getcwd()))
        output = IoTarget(target=lockfile.filename, managed=True)
        format = "json" if lockfile.filename.suffix == '.json' else "yaml"
        Dump.dump_config(lockfile_config, output, format, args.indent, sorted=False)
        return repos_to_lock

    def run(self, args):
        def _filter_enabled(repos):
            return [(k, r) for k, r in repos if not r.operations_disabled]

        args.skip += [
            'setup_dir',
            'repos_apply_patches',
            'setup_environ',
            'write_bbconfig',
        ]

        super().run(args)
        ctx = get_context()
        self.repos = ctx.config.repo_dict.items()
        # when locking, only consider floating repos managed by kas
        repos_to_lock = [(k, r) for k, r in _filter_enabled(self.repos)
                         if not r.commit]
        if not repos_to_lock:
            logging.info('No floating repos found. Nothing to lock.')
            return

        # first update all locks we have without creating new ones
        lockfiles = ctx.config.get_lockfiles()
        for lock in lockfiles:
            repos_to_lock = self._update_lockfile(lock, repos_to_lock, args)
        # then add new locks for the remaining repos to the default lockfile
        if repos_to_lock:
            logging.warning('The following repos are not covered by any '
                            'lockfile. Adding to top lockfile: %s',
                            ', '.join([r.name for _, r in repos_to_lock]))
            lockpath = ctx.config.handler.get_lock_filename()
            if len(lockfiles) and lockfiles[0].filename == lockpath:
                lock = lockfiles[0]
            else:
                lock = ConfigFile(lockpath, True)
                lock.config['header'] = {'version': 14}
                lock.config['overrides'] = {'repos': {}}
            for kr, _ in repos_to_lock:
                lock.config['overrides']['repos'][kr] = {'commit': None}
            self._update_lockfile(lock, repos_to_lock, args)


__KAS_PLUGINS__ = [Lock]
