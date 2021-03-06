# jhbuild - a build script for GNOME 1.x and 2.x
# Copyright (C) 2007  John Stowers
#
#   binary.py: A binary module for executing scripts and installing
#   modules which do not have a configure/autogen/make step
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

__metaclass__ = type

import os

from jhbuild.errors import FatalError, CommandError, BuildStateError
from jhbuild.modtypes import Package, register_module_type, get_dependencies
from jhbuild.utils.unpack import unpack_archive
from jhbuild.modtypes.autotools import AutogenModule
from jhbuild.versioncontrol.tarball import TarballBranch, TarballRepository

__all__ = [ 'Binary' ]

class Binary(AutogenModule):
    '''Represents a binary distributed module which may need to have aditional
    actions operate on it post download'''
    type = 'binary'

    # FIXME: download = 'download' now we have new phases system ??
    PHASE_DOWNLOAD = 'checkout'
    PHASE_UNPACK    = 'unpack'
    PHASE_CONFIGURE = 'configure'
    PHASE_BUILD     = 'build'
    PHASE_INSTALL   = 'install'

    def __init__(self, config, name, version, source_url='', source_size='', source_md5=None,uri=[],
                 patches=[], configure_commands=[], build_commands=[], install_commands=[], 
                 dependencies=[], after=[], suggests=[]):
        self.name = name
        self.version = version

        # create a fake TarballRepository, and give it the moduleset uri
        repo = TarballRepository(config, None, None)
        repo.moduleset_uri = uri
        branch = TarballBranch(repo, source_url, version, None, source_size, source_md5, 
                               None, source_subdir=None, expect_standard_tarball=False)
        branch.patches = patches

        # Set a second-level checkout root, because binary archives often unzip to cwd. We also set
        # expect_standard_tarball to False to allow these packages through.
        branch.checkoutroot = os.path.join(config.checkoutroot, "%s-%s-binary" % (name, version))

        AutogenModule.__init__(self, name, branch, None, None, '', dependencies,
                              after, suggests, supports_non_srcdir_builds=False,
                              skip_autogen=False)

        self.static = True
        self.configure_commands = configure_commands
        self.build_commands = build_commands
        self.install_commands = install_commands

    def _exec_commands(self, buildscript, commands, ignore_failure=False):
        #Commands can contain a number of special placeholders.
        PLACEHOLDERS = {
            "@@PREFIX@@"    :   buildscript.config.prefix,
            "@@SRCDIR@@"    :   self.get_srcdir(buildscript)
            }

        for cmd,cwd,output_file in commands:
            #a command can have many args
            for i in range(0,len(cmd)):
                for k,v in PLACEHOLDERS.items():
                    cmd[i] = cmd[i].replace(k,v)
            #also do replacements in the working directory
            if cwd != None:
                for k,v in PLACEHOLDERS.items():
                    cwd = cwd.replace(k,v)
            # and the output path
            if output_file != None:
                for k,v in PLACEHOLDERS.items():
                    output_file = output_file.replace(k,v)
            try:
                buildscript.execute(cmd, cwd=cwd, output_file=output_file)
            except CommandError, e:
                if not ignore_failure: 
                    raise CommandError(e)

    def checkout(self, buildscript):
        srcdir = self.get_srcdir(buildscript)
        buildscript.set_action(_('Checking out'), self)
        self.branch.checkout(buildscript)

        # FIXME: check for success, but not like this
        #if not os.path.exists(srcdir):
        #    raise BuildStateError(_('source directory %s was not created') % srcdir)

        if self.check_build_policy(buildscript) == self.PHASE_DONE:
            raise SkipToEnd()

    def get_srcdir(self, buildscript):
        # Binary tarballs can have random formats so always unpack into their own dir
        return self.branch.checkoutroot
        
    def do_download(self, buildscript):
        print "DOWNLOAD: ", self.branch.module

    def do_configure(self, buildscript):
        buildscript.set_action('Configure', self)
        self._exec_commands(buildscript, self.configure_commands)
    do_configure.depends = [PHASE_DOWNLOAD]    

    def do_build(self, buildscript):
        buildscript.set_action('Building', self)
        self._exec_commands(buildscript, self.build_commands)
    do_build.depends = [PHASE_CONFIGURE]
    do_build.error_phases = [PHASE_CONFIGURE]

    def do_install(self, buildscript):
        buildscript.set_action('Installing', self)
        self._exec_commands(buildscript, self.install_commands)
        buildscript.packagedb.add(self.name, self.version or '')
        #, 'installed')
    do_install.depends = [PHASE_BUILD]

# only used in this module type at present
def parse_command(node):
    execute = node.getAttribute('execute')
    if node.hasAttribute('cwd'):
        cwd = node.getAttribute('cwd')
    else:
        cwd = None
    if node.hasAttribute('output-file'):
        output_file = node.getAttribute('output-file')
    else:
        output_file = None
    return eval(execute),cwd,output_file

def parse_binary(node, config, uri, repositories, default_repo):
    name = node.getAttribute('id')
    version = node.getAttribute('version')
    source_url = ''
    source_size = None
    source_md5 = None
    patches = []
    configure_commands = []
    build_commands = []
    install_commands = []

    for childnode in node.childNodes:
        if childnode.nodeType != childnode.ELEMENT_NODE: continue
        if childnode.nodeName == 'source':
            source_url = childnode.getAttribute('href')
            if childnode.hasAttribute('size'):
                source_size = int(childnode.getAttribute('size'))
            if childnode.hasAttribute('md5sum'):
                source_md5 = childnode.getAttribute('md5sum')
        elif childnode.nodeName == 'patches':
            for patch in childnode.childNodes:
                if patch.nodeType != patch.ELEMENT_NODE: continue
                if patch.nodeName != 'patch': continue
                patchfile = patch.getAttribute('file')
                if patch.hasAttribute('strip'):
                    patchstrip = int(patch.getAttribute('strip'))
                else:
                    patchstrip = 0
                patches.append((patchfile, patchstrip))
        elif childnode.nodeName == 'configure':
            for cmd in childnode.childNodes:
                if cmd.nodeType != cmd.ELEMENT_NODE: continue
                if cmd.nodeName == 'cmd':
                    configure_commands.append( parse_command(cmd) )
        elif childnode.nodeName == 'build':
            for cmd in childnode.childNodes:
                if cmd.nodeType != cmd.ELEMENT_NODE: continue
                if cmd.nodeName == 'cmd':
                    build_commands.append( parse_command(cmd) )
        elif childnode.nodeName == 'install':
            for cmd in childnode.childNodes:
                if cmd.nodeType != cmd.ELEMENT_NODE: continue
                if cmd.nodeName == 'cmd':
                    install_commands.append( parse_command(cmd) )

    dependencies, after, suggests = get_dependencies(node)

    return Binary(config, name, version, source_url, source_size, source_md5, uri,
                  patches, configure_commands, build_commands, install_commands,
                  dependencies, after, suggests)

register_module_type('binary', parse_binary)

