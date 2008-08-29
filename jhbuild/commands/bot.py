# jhbuild - a build script for GNOME 1.x and 2.x
# Copyright (C) 2001-2006  James Henstridge
# Copyright (C) 2008 Frederic Peters
#
#   bot.py: buildbot control commands
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
#
#
# Some methods are derived from Buildbot own methods (when it was not possible
# to override just some parts of them).  Buildbot is also licensed under the
# GNU General Public License.

import os
import signal
import sys
import urllib
from optparse import make_option
import socket
import __builtin__
import csv

import jhbuild.moduleset
import jhbuild.frontends
from jhbuild.commands import Command, register_command
from jhbuild.commands.base import cmd_build
from jhbuild.config import addpath
from jhbuild.errors import UsageError, FatalError, CommandError

try:
    import buildbot
except ImportError:
    buildbot = None

class cmd_bot(Command):
    doc = _('Control buildbot')

    name = 'bot'
    usage_args = '[ options ... ]'

    def __init__(self):
        Command.__init__(self, [
            make_option('--setup',
                        action='store_true', dest='setup', default=False,
                        help=_('setup a buildbot environment')),
            make_option('--start',
                        action='store_true', dest='start', default=False,
                        help=_('start a buildbot slave server')),
            make_option('--stop',
                        action='store_true', dest='stop', default=False,
                        help=_('stop a buildbot slave server')),
            make_option('--start-server',
                        action='store_true', dest='start_server', default=False,
                        help=_('start a buildbot master server')),
            make_option('--stop-server',
                        action='store_true', dest='stop_server', default=False,
                        help=_('stop a buildbot master server')),
            make_option('--daemon',
                        action='store_true', dest='daemon', default=False,
                        help=_('start as daemon')),
            make_option('--pidfile', metavar='PIDFILE',
                        action='store', dest='pidfile', default=None,
                        help=_('pid file location')),
            make_option('--step',
                        action='store_true', dest='step', default=False,
                        help=_('exec a buildbot step (internal use only)')),
            ])

    def run(self, config, options, args):
        if options.setup:
            return self.setup(config)

        global buildbot
        if buildbot is None:
            import site
            pythonversion = 'python' + str(sys.version_info[0]) + '.' + str(sys.version_info[1])
            pythonpath = os.path.join(config.prefix, 'lib', pythonversion, 'site-packages')
            site.addsitedir(pythonpath)
            if config.use_lib64:
                pythonpath = os.path.join(config.prefix, 'lib64', pythonversion, 'site-packages')
                site.addsitedir(pythonpath)
            try:
                import buildbot
            except ImportError:
                raise FatalError(_('buildbot and twisted not found, run jhbuild bot --setup'))

        # make jhbuild config file accessible to buildbot files
        # (master.cfg , steps.py, etc.)
        __builtin__.__dict__['jhbuild_config'] = config

        daemonize = False
        pidfile = None

        if options.daemon:
            daemonize = True
        if options.pidfile:
            pidfile = options.pidfile


        if options.start:
            return self.start(config, daemonize, pidfile)

        if options.step:
            os.environ['JHBUILDRC'] = config.filename
            os.environ['LC_ALL'] = 'C'
            os.environ['LANGUAGE'] = 'C'
            os.environ['LANG'] = 'C'
            __builtin__.__dict__['_'] = lambda x: x
            config.interact = False
            config.nonetwork = True
            if args[0] == 'update':
                command = 'updateone'
                config.nonetwork = False
            elif args[0] == 'build':
                command = 'buildone'
                config.alwaysautogen = True
            elif args[0] == 'check':
                command = 'buildone'
                config.nobuild = True
                config.makecheck = True
                config.forcecheck = True
                config.build_policy = 'all'
            else:
                command = args[0]
            os.environ['TERM'] = 'dumb'
            rc = jhbuild.commands.run(command, config, args[1:])
            sys.exit(rc)

        if options.start_server:
            return self.start_server(config, daemonize, pidfile)

        if options.stop or options.stop_server:
            return self.stop(config, pidfile)

    def setup(self, config):
        module_set = jhbuild.moduleset.load(config, 'buildbot')
        module_list = module_set.get_module_list('all', config.skip)
        build = jhbuild.frontends.get_buildscript(config, module_list)
        return build.build()
    
    def start(self, config, daemonize, pidfile):
        from twisted.application import service
        application = service.Application('buildslave')
        if ':' in config.jhbuildbot_master:
            master_host, master_port = config.jhbuildbot_master.split(':')
            master_port = int(master_port)
        else:
            master_host, master_port = config.jhbuildbot_master, 9070

        slave_name = config.jhbuildbot_slavename or socket.gethostname()

        keepalive = 600
        usepty = 0
        umask = None
        basedir = os.path.join(config.checkoutroot, 'jhbuildbot')
        if not os.path.exists(os.path.join(basedir, 'builddir')):
            os.makedirs(os.path.join(basedir, 'builddir'))
        os.chdir(basedir)

        from buildbot.slave.bot import BuildSlave
        s = BuildSlave(master_host, master_port,
                slave_name, config.jhbuildbot_password, basedir,
                keepalive, usepty, umask=umask)
        s.setServiceParent(application)


        from twisted.scripts._twistd_unix import UnixApplicationRunner, ServerOptions

        opts = ['--no_save']
        if not daemonize:
            opts.append('--nodaemon')
        if pidfile:
            opts.extend(['--pidfile', pidfile])
        options = ServerOptions()
        options.parseOptions(opts)

        class JhBuildbotApplicationRunner(UnixApplicationRunner):
            application = None

            def createOrGetApplication(self):
                return self.application

        JhBuildbotApplicationRunner.application = application
        JhBuildbotApplicationRunner(options).run()

    def start_server(self, config, daemonize, pidfile):

        from twisted.scripts._twistd_unix import UnixApplicationRunner, ServerOptions

        opts = ['--no_save']
        if not daemonize:
            opts.append('--nodaemon')
        if pidfile:
            opts.extend(['--pidfile', pidfile])
        options = ServerOptions()
        options.parseOptions(opts)

        class JhBuildbotApplicationRunner(UnixApplicationRunner):
            application = None

            def createOrGetApplication(self):
                return self.application

        from twisted.application import service, strports
        from buildbot.master import BuildMaster
        application = service.Application('buildmaster')
        from buildbot.buildslave import BuildSlave

        from twisted.python import log
        from twisted.internet import defer
        from buildbot import interfaces
        from buildbot.process.properties import Properties


        class JhBuildMaster(BuildMaster):
            jhbuild_config = config
            def loadConfig(self, f):
                # modified from parent method to get slaves, projects, change
                # sources, schedulers, builders and web status ouf of
                # master.cfg [it would have been cleaner if jhbuild didn't
                # have to copy all that code.]
                localDict = {'basedir': os.path.expanduser(self.basedir)}
                try:
                    exec f in localDict
                except:
                    log.msg("error while parsing config file")
                    raise

                try:
                    config = localDict['BuildmasterConfig']
                except KeyError:
                    log.err("missing config dictionary")
                    log.err("config file must define BuildmasterConfig")
                    raise

                known_keys = ("bots", "slaves",
                              "sources", "change_source",
                              "schedulers", "builders",
                              "slavePortnum", "debugPassword", "manhole",
                              "status", "projectName", "projectURL", "buildbotURL",
                              "properties"
                              )
                for k in config.keys():
                    if k not in known_keys:
                        log.msg("unknown key '%s' defined in config dictionary" % k)

                # the 'slaves' list is read from the 'slaves.csv' file in the
                # current directory it is a CSV file structured like this:
                #   slavename,password
                # it is also possible to define build slave options, for
                # example:
                #   slavename,password,max_builds=2
                # (recognized build slave options are max_build and
                # missing_timeout)
                config['slaves'] = []
                if os.path.exists('slaves.csv'):
                    for x in csv.reader(file('slaves.csv')):
                        if not x or x[0].startswith('#'):
                            continue
                        kw = {}
                        for option in x[2:]:
                            if not '=' in option:
                                continue
                            k, v = option.split('=', 1)
                            if k in ('max_builds', 'missing_timeout'):
                                v = int(v)
                            else:
                                # unrecognized option
                                continue
                            kw[k] = v
                        config['slaves'].append(BuildSlave(x[0], x[1], **kw))

                if len(config['slaves']) == 0:
                    log.msg('you must fill slaves.csv with slaves')

                module_set = jhbuild.moduleset.load(self.jhbuild_config)
                module_list = module_set.get_module_list(
                        self.jhbuild_config.modules,
                        self.jhbuild_config.skip,
                        include_optional_modules=True)
                config['projects'] = [x.name for x in module_list \
                                      if not x.name.startswith('meta-')]

                if self.jhbuild_config.jhbuildbot_svn_commits_box:
                    # trigger builds from mails to svn-commit-list
                    # (note Maildir must be correct, or everything will fail)
                    from jhbuild.buildbot.changes import GnomeMaildirSource
                    config['change_source'] = GnomeMaildirSource(
                            self.jhbuild_config.jhbuildbot_svn_commits_box,
                            prefix=None)
                else:
                    # support injection (use 'buildbot sendchange')
                    from buildbot.changes.pb import PBChangeSource
                    c['change_source'] = PBChangeSource()

                # Schedulers
                from jhbuild.buildbot.scheduler import SerialScheduler, OnCommitScheduler
                config['schedulers'] = []
                for slave in config['slaves']:
                    s = None
                    for project in config['projects']:
                        buildername = str('%s-%s' % (project, slave.slavename))
                        s = SerialScheduler(buildername, project, upstream=s,
                                            builderNames=[buildername])
                        config['schedulers'].append(s)
                        if self.jhbuild_config.jhbuildbot_svn_commits_box:
                            # schedulers that will launch job when receiving
                            # change notifications
                            s2 = OnCommitScheduler('oc-' + buildername,
                                    project, builderNames=[buildername])
                            config['schedulers'].append(s2)

                # Builders
                from jhbuild.buildbot.factory import JHBuildFactory
                config['builders'] = []
                for project in config['projects']:
                    for slave in config['slaves']:
                        f = JHBuildFactory(project)
                        config['builders'].append({
                            'name' : "%s-%s" % (project, slave.slavename),
                            'slavename' : slave.slavename,
                            'builddir' : 'builddir/%s.%s' % (project, slave.slavename),
                            'factory' : f,
                            'category' : project
                        })

                # Status targets
                if not config.has_key('status'):
                    # let it be possible to define additional status in
                    # master.cfg
                    config['status'] = []

                from jhbuild.buildbot.status.web import JHBuildWebStatus
                config['status'].append(
                    JHBuildWebStatus(
                        self.jhbuild_config.moduleset,
                        config['projects'],
                        [x.slavename for x in config['slaves']],
                        http_port=8080, allowForce=True)
                )

                # remaining of the method is a straight copy from buildbot
                # ...
                try:
                    # required
                    schedulers = config['schedulers']
                    builders = config['builders']
                    for k in builders:
                        if k['name'].startswith("_"):
                            errmsg = ("builder names must not start with an "
                                      "underscore: " + k['name'])
                            log.err(errmsg)
                            raise ValueError(errmsg)

                    slavePortnum = config['slavePortnum']
                    #slaves = config['slaves']
                    #change_source = config['change_source']

                    # optional
                    debugPassword = config.get('debugPassword')
                    manhole = config.get('manhole')
                    status = config.get('status', [])
                    projectName = config.get('projectName')
                    projectURL = config.get('projectURL')
                    buildbotURL = config.get('buildbotURL')
                    properties = config.get('properties', {})

                except KeyError, e:
                    log.msg("config dictionary is missing a required parameter")
                    log.msg("leaving old configuration in place")
                    raise

                #if "bots" in config:
                #    raise KeyError("c['bots'] is no longer accepted")

                slaves = config.get('slaves', [])
                if "bots" in config:
                    m = ("c['bots'] is deprecated as of 0.7.6 and will be "
                         "removed by 0.8.0 . Please use c['slaves'] instead.")
                    log.msg(m)
                    warnings.warn(m, DeprecationWarning)
                    for name, passwd in config['bots']:
                        slaves.append(BuildSlave(name, passwd))

                if "bots" not in config and "slaves" not in config:
                    log.msg("config dictionary must have either 'bots' or 'slaves'")
                    log.msg("leaving old configuration in place")
                    raise KeyError("must have either 'bots' or 'slaves'")

                #if "sources" in config:
                #    raise KeyError("c['sources'] is no longer accepted")

                change_source = config.get('change_source', [])
                if isinstance(change_source, (list, tuple)):
                    change_sources = change_source
                else:
                    change_sources = [change_source]
                if "sources" in config:
                    m = ("c['sources'] is deprecated as of 0.7.6 and will be "
                         "removed by 0.8.0 . Please use c['change_source'] instead.")
                    log.msg(m)
                    warnings.warn(m, DeprecationWarning)
                    for s in config['sources']:
                        change_sources.append(s)

                # do some validation first
                for s in slaves:
                    assert isinstance(s, BuildSlave)
                    if s.slavename in ("debug", "change", "status"):
                        raise KeyError, "reserved name '%s' used for a bot" % s.slavename
                if config.has_key('interlocks'):
                    raise KeyError("c['interlocks'] is no longer accepted")

                assert isinstance(change_sources, (list, tuple))
                for s in change_sources:
                    assert interfaces.IChangeSource(s, None)
                # this assertion catches c['schedulers'] = Scheduler(), since
                # Schedulers are service.MultiServices and thus iterable.
                errmsg = "c['schedulers'] must be a list of Scheduler instances"
                assert isinstance(schedulers, (list, tuple)), errmsg
                for s in schedulers:
                    assert interfaces.IScheduler(s, None), errmsg
                assert isinstance(status, (list, tuple))
                for s in status:
                    assert interfaces.IStatusReceiver(s, None)

                slavenames = [s.slavename for s in slaves]
                buildernames = []
                dirnames = []
                for b in builders:
                    if type(b) is tuple:
                        raise ValueError("builder %s must be defined with a dict, "
                                         "not a tuple" % b[0])
                    if b.has_key('slavename') and b['slavename'] not in slavenames:
                        raise ValueError("builder %s uses undefined slave %s" \
                                         % (b['name'], b['slavename']))
                    for n in b.get('slavenames', []):
                        if n not in slavenames:
                            raise ValueError("builder %s uses undefined slave %s" \
                                             % (b['name'], n))
                    if b['name'] in buildernames:
                        raise ValueError("duplicate builder name %s"
                                         % b['name'])
                    buildernames.append(b['name'])
                    if b['builddir'] in dirnames:
                        raise ValueError("builder %s reuses builddir %s"
                                         % (b['name'], b['builddir']))
                    dirnames.append(b['builddir'])

                unscheduled_buildernames = buildernames[:]
                schedulernames = []
                for s in schedulers:
                    for b in s.listBuilderNames():
                        assert b in buildernames, \
                               "%s uses unknown builder %s" % (s, b)
                        if b in unscheduled_buildernames:
                            unscheduled_buildernames.remove(b)

                    if s.name in schedulernames:
                        # TODO: schedulers share a namespace with other Service
                        # children of the BuildMaster node, like status plugins, the
                        # Manhole, the ChangeMaster, and the BotMaster (although most
                        # of these don't have names)
                        msg = ("Schedulers must have unique names, but "
                               "'%s' was a duplicate" % (s.name,))
                        raise ValueError(msg)
                    schedulernames.append(s.name)

                if unscheduled_buildernames:
                    log.msg("Warning: some Builders have no Schedulers to drive them:"
                            " %s" % (unscheduled_buildernames,))

                # assert that all locks used by the Builds and their Steps are
                # uniquely named.
                locks = {}
                for b in builders:
                    for l in b.get('locks', []):
                        if locks.has_key(l.name):
                            if locks[l.name] is not l:
                                raise ValueError("Two different locks (%s and %s) "
                                                 "share the name %s"
                                                 % (l, locks[l.name], l.name))
                        else:
                            locks[l.name] = l
                    # TODO: this will break with any BuildFactory that doesn't use a
                    # .steps list, but I think the verification step is more
                    # important.
                    for s in b['factory'].steps:
                        for l in s[1].get('locks', []):
                            if locks.has_key(l.name):
                                if locks[l.name] is not l:
                                    raise ValueError("Two different locks (%s and %s)"
                                                     " share the name %s"
                                                     % (l, locks[l.name], l.name))
                            else:
                                locks[l.name] = l

                if not isinstance(properties, dict):
                    raise ValueError("c['properties'] must be a dictionary")

                # slavePortnum supposed to be a strports specification
                if type(slavePortnum) is int:
                    slavePortnum = "tcp:%d" % slavePortnum

                # now we're committed to implementing the new configuration, so do
                # it atomically
                # TODO: actually, this is spread across a couple of Deferreds, so it
                # really isn't atomic.

                d = defer.succeed(None)

                self.projectName = projectName
                self.projectURL = projectURL
                self.buildbotURL = buildbotURL
                
                self.properties = Properties()
                self.properties.update(properties, self.configFileName)

                # self.slaves: Disconnect any that were attached and removed from the
                # list. Update self.checker with the new list of passwords, including
                # debug/change/status.
                d.addCallback(lambda res: self.loadConfig_Slaves(slaves))

                # self.debugPassword
                if debugPassword:
                    self.checker.addUser("debug", debugPassword)
                    self.debugPassword = debugPassword

                # self.manhole
                if manhole != self.manhole:
                    # changing
                    if self.manhole:
                        # disownServiceParent may return a Deferred
                        d.addCallback(lambda res: self.manhole.disownServiceParent())
                        def _remove(res):
                            self.manhole = None
                            return res
                        d.addCallback(_remove)
                    if manhole:
                        def _add(res):
                            self.manhole = manhole
                            manhole.setServiceParent(self)
                        d.addCallback(_add)

                # add/remove self.botmaster.builders to match builders. The
                # botmaster will handle startup/shutdown issues.
                d.addCallback(lambda res: self.loadConfig_Builders(builders))

                d.addCallback(lambda res: self.loadConfig_status(status))

                # Schedulers are added after Builders in case they start right away
                d.addCallback(lambda res: self.loadConfig_Schedulers(schedulers))
                # and Sources go after Schedulers for the same reason
                d.addCallback(lambda res: self.loadConfig_Sources(change_sources))

                # self.slavePort
                if self.slavePortnum != slavePortnum:
                    if self.slavePort:
                        def closeSlavePort(res):
                            d1 = self.slavePort.disownServiceParent()
                            self.slavePort = None
                            return d1
                        d.addCallback(closeSlavePort)
                    if slavePortnum is not None:
                        def openSlavePort(res):
                            self.slavePort = strports.service(slavePortnum,
                                                              self.slaveFactory)
                            self.slavePort.setServiceParent(self)
                        d.addCallback(openSlavePort)
                        log.msg("BuildMaster listening on port %s" % slavePortnum)
                    self.slavePortnum = slavePortnum

                log.msg("configuration update started")
                def _done(res):
                    self.readConfig = True
                    log.msg("configuration update complete")
                d.addCallback(_done)
                d.addCallback(lambda res: self.botmaster.maybeStartAllBuilds())
                return d

        basedir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                '../../buildbot/')
        os.chdir(basedir)
        if not os.path.exists(os.path.join(basedir, 'builddir')):
            os.makedirs(os.path.join(basedir, 'builddir'))
        master_cfg_path = os.path.join(basedir, 'master.cfg')

        JhBuildMaster(basedir, master_cfg_path).setServiceParent(application)

        JhBuildbotApplicationRunner.application = application
        JhBuildbotApplicationRunner(options).run()

    def stop(self, config, pidfile):
        try:
            pid = int(file(pidfile).read())
        except:
            raise FatalError(_('failed to get buildbot PID'))

        os.kill(pid, signal.SIGTERM)


register_command(cmd_bot)
