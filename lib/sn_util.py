#!/usr/bin/python2
# -*- coding: utf-8; tab-width: 4; indent-tabs-mode: t -*-

import os
import logging
import shutil
import subprocess
import pwd
import socket
import re
from gi.repository import GLib

class SnUtil:

	@staticmethod
	def addLinePrefix(tstr, prefix):
		return prefix + ("\n" + prefix).join(tstr.split("\n"))

	@staticmethod
	def getSysctl(name):
		msg = SnUtil.shell("/sbin/sysctl -n %s"%(name), "stdout")
		return msg.rstrip('\n')

	@staticmethod
	def setSysctl(name, value):
		return

	@staticmethod
	def copyToDir(srcFilename, dstdir, mode=None):
		"""Copy file to specified directory, and set file mode if required"""

		if not os.path.isdir(dstdir):
			os.makedirs(dstdir)
		fdst = os.path.join(dstdir, os.path.basename(srcFilename))
		shutil.copy(srcFilename, fdst)
		if mode is not None:
			SnUtil.shell("/bin/chmod " + mode + " \"" + fdst + "\"")

	@staticmethod
	def copyToFile(srcFilename, dstFilename, mode=None):
		"""Copy file to specified filename, and set file mode if required"""

		if not os.path.isdir(os.path.dirname(dstFilename)):
			os.makedirs(os.path.dirname(dstFilename))
		shutil.copy(srcFilename, dstFilename)
		if mode is not None:
			SnUtil.shell("/bin/chmod " + mode + " \"" + dstFilename + "\"")

	@staticmethod
	def readFile(filename):
		"""Read file, returns the whold content"""

		f = open(filename, 'r')
		buf = f.read()
		f.close()
		return buf

	@staticmethod
	def writeFile(filename, buf, mode=None):
		"""Write buffer to file"""

		f = open(filename, 'w')
		f.write(buf)
		f.close()
		if mode is not None:
			SnUtil.shell("/bin/chmod " + mode + " \"" + filename + "\"")

	@staticmethod
	def mkDir(dirname):
		if not os.path.isdir(dirname):
			SnUtil.forceDelete(dirname)
			os.mkdir(dirname)

	@staticmethod
	def mkDirAndClear(dirname):
		SnUtil.forceDelete(dirname)
		os.mkdir(dirname)

	@staticmethod
	def touchFile(filename):
		assert not os.path.exists(filename)
		f = open(filename, 'w')
		f.close()

	@staticmethod
	def forceDelete(filename):
		if os.path.islink(filename):
			os.remove(filename)
		elif os.path.isfile(filename):
			os.remove(filename)
		elif os.path.isdir(filename):
			shutil.rmtree(filename)

	@staticmethod
	def forceSymlink(source, link_name):
		if os.path.exists(link_name):
			os.remove(link_name)
		os.symlink(source, link_name)

	@staticmethod
	def getFreeSocketPort(portType, portStart, portEnd):
		if portType == "tcp":
			sType = socket.SOCK_STREAM
		elif portType == "udp":
			assert False
		else:
			assert False

		for port in range(portStart, portEnd+1):
			s = socket.socket(socket.AF_INET, sType)
			try:
				s.bind((('', port)))
				return port
			except socket.error:
				continue
			finally:
				s.close()
		raise Exception("No valid %s port in [%d,%d]."%(portType, portStart, portEnd))

	@staticmethod
	def shell(cmd, flags=""):
		"""Execute shell command"""

		assert cmd.startswith("/")

		# Execute shell command, throws exception when failed
		if flags == "":
			retcode = subprocess.Popen(cmd, shell = True).wait()
			if retcode != 0:
				raise Exception("Executing shell command \"%s\" failed, return code %d"%(cmd, retcode))
			return

		# Execute shell command, throws exception when failed, returns stdout+stderr
		if flags == "stdout":
			proc = subprocess.Popen(cmd,
				                    shell = True,
				                    stdout = subprocess.PIPE,
				                    stderr = subprocess.STDOUT)
			out = proc.communicate()[0]
			if proc.returncode != 0:
				raise Exception("Executing shell command \"%s\" failed, return code %d"%(cmd, proc.returncode))
			return out

		# Execute shell command, returns (returncode,stdout+stderr)
		if flags == "retcode+stdout":
			proc = subprocess.Popen(cmd,
				                    shell = True,
				                    stdout = subprocess.PIPE,
				                    stderr = subprocess.STDOUT)
			out = proc.communicate()[0]
			return (proc.returncode, out)

		assert False

	@staticmethod
	def shellInteractive(cmd, strInput, flags=""):
		"""Execute shell command with input interaction"""

		assert cmd.startswith("/")

		# Execute shell command, throws exception when failed
		if flags == "":
			proc = subprocess.Popen(cmd,
									shell = True,
									stdin = subprocess.PIPE)
			proc.communicate(strInput)
			if proc.returncode != 0:
				raise Exception("Executing shell command \"%s\" failed, return code %d"%(cmd, proc.returncode))
			return

		# Execute shell command, throws exception when failed, returns stdout+stderr
		if flags == "stdout":
			proc = subprocess.Popen(cmd,
									shell = True,
									stdin = subprocess.PIPE,
									stdout = subprocess.PIPE,
									stderr = subprocess.STDOUT)
			out = proc.communicate(strInput)[0]
			if proc.returncode != 0:
				raise Exception("Executing shell command \"%s\" failed, return code %d, output %s"%(cmd, proc.returncode, out))
			return out

		# Execute shell command, returns (returncode,stdout+stderr)
		if flags == "retcode+stdout":
			proc = subprocess.Popen(cmd,
									shell = True,
									stdin = subprocess.PIPE,
									stdout = subprocess.PIPE,
									stderr = subprocess.STDOUT)
			out = proc.communicate(strInput)[0]
			return (proc.returncode, out)

		assert False

	@staticmethod
	def ipMaskToLen(mask):
		"""255.255.255.0 -> 24"""

		netmask = 0
		netmasks = mask.split('.')
		for i in range(0,len(netmasks)):
			netmask *= 256
			netmask += int(netmasks[i])
		return 32 - (netmask ^ 0xFFFFFFFF).bit_length()

	@staticmethod
	def dropPriviledgeTo(userName):
		assert os.getuid() == 0
		pwobj = pwd.getpwnam(userName)
	    os.setgid(pwobj.pw_gid)
	    os.setuid(pwobj.pw_uid)

	@staticmethod
	def euidInvoke(userName, func, *args):
		if userName is not None:
			oldeuid = os.geteuid()
			oldegid = os.getegid()
			pwobj = pwd.getpwnam(userName)
			try:
				os.setegid(pwobj.pw_gid)
				os.seteuid(pwobj.pw_uid)

				return func(*args)
			finally:
				os.seteuid(oldeuid)
				os.setegid(oldegid)
		else:
			return func(*args)

	@staticmethod
	def idleInvoke(func, *args):
		def _idleCallback(func, *args):
			func(*args)
			return False
		GLib.idle_add(_idleCallback, func, *args)

	@staticmethod
	def checkSshPubKey(pubkey, keyType, userName, hostName):
		if keyType == "rsa":
			prefix = "ssh-rsa"
		elif keyType == "dsa":
			prefix = "ssh-dss"
		elif keyType == "ecdsa":
			prefix = "ecdsa-sha2-nistp256"
		else:
			assert False

		strList = pubkey.split()
		if len(strList) != 3:
			return False
		if strList[0] != prefix:
			return False
		if strList[2] != "%s@%s"%(userName, hostName):
			return False
		return True

	@staticmethod
	def initSshKeyFile(keyType, userName, hostName, privkeyFile, pubkeyFile):
		needInit = False
		if not os.path.exists(privkeyFile) or not os.path.exists(pubkeyFile):
			needInit = True
		if os.path.exists(pubkeyFile):
			with open(pubkeyFile, "rt") as f:
				pubkey = f.read()
				if not SnUtil.checkSshPubKey(pubkey, keyType, userName, hostName):
					needInit = True

		if needInit:
			comment = "%s@%s"%(userName, hostName)
			SnUtil.forceDelete(privkeyFile)
			SnUtil.forceDelete(pubkeyFile)

			# fixme don't know why euid can't be child's uid
			#SnUtil.shell("/bin/ssh-keygen -t %s -N \"\" -C \"%s\" -f \"%s\" -q"%(keyType, comment, privkeyFile), "stdout")
			SnUtil.shell("/usr/bin/su -m %s -c \"/bin/ssh-keygen -t %s -N \\\"\\\" -C \\\"%s\\\" -f \\\"%s\\\" -q\""%(userName, keyType, comment, privkeyFile), "stdout")

			assert os.path.exists(privkeyFile) and os.path.exists(pubkeyFile)

	@staticmethod
	def getSslSocketPeerName(sslSock):
		cert = sslSock.get_peer_certificate()
		if cert is None:
			return None
		subject = cert.get_subject()
		if subject is None:
			return None
		return subject.CN

	@staticmethod
	def getPidBySocket(socketInfo):
		"""need to be run by root. socketInfo is like 0.0.0.0:80"""

		rc, ret = SnUtil.shell("/bin/netstat -anp | grep \"%s\""%(socketInfo), "retcode+stdout")
		if rc != 0:
			return -1
		print ret

		m = re.search(" +([0-9]+)/.*$", ret, re.MULTILINE)
		assert m is not None
		return int(m.group(1))

	@staticmethod
	def dbusGetUserName(connection, sender):
		if sender is None:
			return None
		uid = connection.get_unix_user(sender)
		return pwd.getpwuid(uid).pw_name

	@staticmethod
	def cbConditionToStr(cb_condition):
	    ret = ""
	    if cb_condition & GLib.IO_IN:
	            ret += "IN "
	    if cb_condition & GLib.IO_OUT:
	            ret += "OUT "
	    if cb_condition & GLib.IO_PRI:
	            ret += "PRI "
	    if cb_condition & GLib.IO_ERR:
	            ret += "ERR "
	    if cb_condition & GLib.IO_HUP:
	            ret += "HUP "
	    if cb_condition & GLib.IO_NVAL:
	            ret += "NVAL "
	    return ret

	@staticmethod
	def getLoggingLevel(logLevel):
		if logLevel == "CRITICAL":
			return logging.CRITICAL
		elif logLevel == "ERROR":
			return logging.ERROR
		elif logLevel == "WARNING":
			return logging.WARNING
		elif logLevel == "INFO":
			return logging.INFO
		elif logLevel == "DEBUG":
			return logging.DEBUG
		else:
			assert False

class SnSleepNotifier:

	SLEEP_TYPE_SUSPEND = 0
	SLEEP_TYPE_HIBERNATE = 1
	SLEEP_TYPE_HYBRID_SLEEP = 2

	def __init__(self, cbBeforeSleep, cbAfterResume):
		self.cbBeforeSleep = cbBeforeSleep
		self.cbAfterResume = cbAfterResume

	def dispose(self):
		pass

