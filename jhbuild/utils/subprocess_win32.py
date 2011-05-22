# jhbuild - a build script for GNOME 1.x and 2.x
# Copyright (C) 2001-2006  James Henstridge
#
#   subprocess_win32: monkeypatch to make jhbuild work on win32
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

import os
import sys
import subprocess as real_subprocess

PIPE = real_subprocess.PIPE
STDOUT = real_subprocess.STDOUT

def fix_path_for_msys(oldpath):
    return oldpath.replace('\\','/')


def cmdline2list(cmd_string):
    """
    Translate a command line string into a sequence of arguments, with
    the same rules as subprocess.list2cmdline:

    1) Arguments are delimited by white space, which is either a
       space or a tab.

    2) A string surrounded by double quotation marks is
       interpreted as a single argument, regardless of white space
       or pipe characters contained within.  A quoted string can be
       embedded in an argument.

    3) A double quotation mark preceded by a backslash is
       interpreted as a literal double quotation mark.

    4) Backslashes are interpreted literally, unless they
       immediately precede a double quotation mark.

    5) If backslashes immediately precede a double quotation mark,
       every pair of backslashes is interpreted as a literal
       backslash.  If the number of backslashes is odd, the last
       backslash escapes the next double quotation mark as
       described in rule 3.
    """

    cmd_string = cmd_string.strip()

    result = []
    current_element = ""
    escape = False
    in_quotes = False
    for character in cmd_string:
        if escape:
            if character != '"':
                current_element += '\\'
            current_element += character
            escape = False
        else:
            if character=='\\':
                escape = True
            elif character=='"':
                if in_quotes: in_quotes = False
                else:         in_quotes = True
            elif (character==' ' or character==9) and not in_quotes:
                result.append(current_element)
                current_element = ""
            else:
                current_element += character
    if escape:
        current_element += '\\'
    result.append(current_element)
    return result

list2cmdline = real_subprocess.list2cmdline

def route_through_shell(path, command):
    program = os.path.abspath(os.path.join(path, command[0]))
    return ['sh.exe', '-c', ' '.join([program] + command[1:])]

def route_through_cmd(path, command):
    program = os.path.abspath(os.path.join(path, command[0]))
    return [program] + command[1:]

class Popen(real_subprocess.Popen):
    __emulate_close_fds = False

    def __init__(self, command, **kws):
        # Force the command line to be a list so we can mess with it
        if not isinstance(command, list):
            command = cmdline2list(command)

        if command[0] == '/bin/sh':
            command[0] = 'sh'

        # ./ confuses windows, and these are normally shell scripts so use
        # sh.exe
        if command[0] != 'sh':
            if command[0].startswith('./'):
                kws['shell'] = False
                command = route_through_shell(kws.get('cwd', ''), command)
            elif not command[0].endswith('.exe') or kws['shell']:
                # Check if program is actually a script
                for path in os.environ['PATH'].split(os.pathsep):
                    prog = os.path.abspath(os.path.join(path, command[0]))
                    if os.path.exists(prog + '.bat'):
                        kws['shell'] = True
                        command = route_through_cmd(path, command)
                        break
                    if os.path.exists(prog) or os.path.exists(prog + ".sh"):
                        kws['shell'] = False
                        command = route_through_shell(path, command)
                        break


        # fix all backslashes to forward slashes - MSYS is smart about doing
        # this but we're not always running things via sh.exe.
        for i in range(0,len(command)):
            command[i] = fix_path_for_msys(command[i])

        # default Windows implementation of close_fds is useless, we have to
        # emulate it
        if 'close_fds' in kws and kws['close_fds']:
            kws['close_fds'] = False
            self.__emulate_close_fds = True

        real_subprocess.Popen.__init__(self, command, **kws)

    def __del__(self):
        if self.__emulate_close_fds:
            for f in self.stdin, self.stdout, self.stderr:
                # This somehow tells us if we are dealing with a pipe.
                if isinstance(f, file) and f.name == '<fdopen>':
                    # Not that it actually matters.
                    f.close()
            sys.stdout.flush()
        real_subprocess.Popen.__del__(self)
