#!/usr/bin/python2
# -*- coding: utf-8; tab-width: 4; indent-tabs-mode: t -*-

import os
import shutil
import socket
import logging
import traceback
import strict_pgs
from gi.repository import GLib

from sn_util import SnUtil
from sn_util import SnSleepNotifier
from sn_sub_proc import SnSubProcess
from sn_conn_local import SnLocalServer
from sn_module import SnRejectException

"""
ModuleInstance FSM trigger table:
  (STATE_INIT is the initial state)

  STATE_INIT        -> STATE_INACTIVE    : initialized
  STATE_INACTIVE    -> STATE_ACTIVE      : peer added, peer module added
  STATE_ACTIVE      -> STATE_INACTIVE    : peer removed, peer module removed

  STATE_ACTIVE      -> STATE_REJECT      : onRecv raise SnRejectException
  STATE_ACTIVE      -> STATE_PEER_REJECT : reject received
  STATE_REJECT      -> STATE_INACTIVE    : peer removed, peer module removed
  STATE_PEER_REJECT -> STATE_INACTIVE    : peer removed, peer module removed

  STATE_INIT        -> STATE_EXCEPT      : onInit raise exception
  STATE_INACTIVE    -> STATE_EXCEPT      : onActive raise exception
  STATE_ACTIVE      -> STATE_EXCEPT      : onRecv / onInactive raise exception
  STATE_ACTIVE      -> STATE_PEER_EXCEPT : except received
  STATE_REJECT      -> STATE_EXCEPT      : onInactive raise exception
  STATE_PEER_EXCEPT -> STATE_INACTIVE    : peer removed, peer module removed

"""

"""
ModuleInstance FSM event callback table:
  (module has no way to control the state change, it can only respond to it)

  STATE_INIT     -> STATE_INACTIVE    : call onInit        BEFORE state change
  STATE_INACTIVE -> STATE_ACTIVE      : call onActive      AFTER state change
  STATE_ACTIVE   -> STATE_INACTIVE    : call onInactive    AFTER state change
  STATE_ACTIVE   -> STATE_REJECT      : call onInactive    AFTER state change
  STATE_ACTIVE   -> STATE_PEER_REJECT : call onInactive    AFTER state change
  STATE_ACTIVE   -> STATE_PEER_EXCEPT : call onInactive    AFTER state change

"""

"""
ModuleInstance FSM sendReject / sendExcept table:

  STATE_INIT        -> onInit     -> excGeneral   -> STATE_EXCEPT
  STATE_ACTIVE      -> onActive   -> excGeneral   -> STATE_EXCEPT -> sendExcept
  STATE_ACTIVE      -> onRecv     -> excGeneral   -> STATE_EXCEPT -> sendExcept
  STATE_INACTIVE    -> onInactive -> excGeneral   -> STATE_EXCEPT
  STATE_PEER_REJECT -> onInactive -> excGeneral   -> STATE_EXCEPT
  STATE_PEER_EXCEPT -> onInactive -> excGeneral   -> STATE_EXCEPT
  STATE_REJECT      -> onInactive -> return                       -> sendReject
  STATE_REJECT      -> onInactive -> excGeneral   -> STATE_EXCEPT -> sendExcept

"""

# fixme: needs to consider user change, both local user change and user change received by peer

class SnSysInfo:
	userList = None					# list<SnSysInfoUser>
	moduleList = None				# list<SnSysInfoModule>

class SnSysInfoUser:
	userName = None					# str

	def __eq__(self, other):
		return isinstance(other, self.__class__) and self.userName == other.userName
	def __ne__(self, other):
		return not self.__eq__(other)
	def __hash__(self):
		return hash(self.userName)

class SnSysInfoModule:
	moduleName = None				# str
	userName = None					# str

	def __eq__(self, other):
		return isinstance(other, self.__class__) and self.moduleName == other.moduleName and self.userName == other.userName
	def __ne__(self, other):
		return not self.__eq__(other)
	def __hash__(self):
		return hash(self.moduleName) ^ hash(self.userName)

class SnDataPacket:
	srcUserName = None				# str, can be None
	srcModuleName = None			# str
	data = None						# object

class SnDataPacketReject:
	message = None					# str

class SnDataPacketExcept:
	pass

class SnLocalManager:

	WORK_STATE_IDLE = 0
	WORK_STATE_WORKING = 1

	def __init__(self, param):
		logging.debug("SnLocalManager.__init__: Start")

		# variables
		self.param = param
		self.localInfo = self._getLocalInfo()
		self.moduleObjDict = self._getModuleObjDict()
		self.sleepNotifier = SnSleepNotifier(self.onBeforeSleep, self.onAfterResume)

		# active local peers
		GLib.idle_add(self._idleLocalPeerActive)

		logging.debug("SnLocalManager.__init__: End")
		return

	def dispose(self):
		logging.debug("SnLocalManager.dispose: Start")

		# set modules of local peer to inactive state
		if socket.gethostname() in self.moduleObjDict:
			self.onPeerRemove(socket.gethostname())

		# check modules' state
		for moiList in self.moduleObjDict.values():
			for moi in moiList:
				assert moi.state in [ _ModuleInfoInternal.STATE_EXCEPT, _ModuleInfoInternal.STATE_INACTIVE ]

		logging.debug("SnLocalManager.dispose: End")
		return

	def getLocalInfo(self):
		return self.localInfo

	def getWorkState(self):
		for moiList in self.moduleObjDict.values():
			for moi in moiList:
				if moi.workState == _ModuleInfoInternal.WORK_STATE_WORKING:
					return SnLocalManager.WORK_STATE_WORKING
		return SnLocalManager.WORK_STATE_IDLE

	def getModuleKeyList(self):
		ret = []
		for moiList in self.moduleObjDict.values():
			for moi in moiList:
				ret.append((moi.peerName, moi.userName, moi.moduleName))
		return ret

	def getModuleState(self, peerName, userName, moduleName):
		moi = self._getMoi(peerName, userName, moduleName)
		return (moi.state, moi.failMessage)

	##### event callback ####

	def onPeerChange(self, peerName, peerInfo):
		logging.debug("SnLocalManager.onPeerChange: Start, %s", peerName)

		# no peer module
		for moi in self.moduleObjDict[peerName]:
			if not self._matchPmi(peerName, peerInfo, moi):
				if moi.state == _ModuleInfoInternal.STATE_INIT:
					pass
				elif moi.state == _ModuleInfoInternal.STATE_ACTIVE:
					self._moiChangeState(moi, _ModuleInfoInternal.STATE_INACTIVE)
					self._moiCallFunc(moi, "onInactive")
				elif moi.state == _ModuleInfoInternal.STATE_INACTIVE:
					pass
				elif moi.state == _ModuleInfoInternal.STATE_REJECT:
					self._moiChangeState(moi, _ModuleInfoInternal.STATE_INACTIVE)
				elif moi.state == _ModuleInfoInternal.STATE_PEER_REJECT:
					self._moiChangeState(moi, _ModuleInfoInternal.STATE_INACTIVE)
				elif moi.state == _ModuleInfoInternal.STATE_EXCEPT:
					pass
				elif moi.state == _ModuleInfoInternal.STATE_PEER_EXCEPT:
					self._moiChangeState(moi, _ModuleInfoInternal.STATE_INACTIVE)
				else:
					assert False

		# has peer module
		for pmi in peerInfo.moduleList:
			moi = self._findMoiMapped(peerName, pmi.userName, pmi.moduleName)
			if moi is not None:
				if moi.state == _ModuleInfoInternal.STATE_INIT:
					pass
				elif moi.state == _ModuleInfoInternal.STATE_ACTIVE:
					pass
				elif moi.state == _ModuleInfoInternal.STATE_INACTIVE:
					self._moiChangeState(moi, _ModuleInfoInternal.STATE_ACTIVE)
					self._moiCallFunc(moi, "onActive")
				elif moi.state == _ModuleInfoInternal.STATE_REJECT:
					pass
				elif moi.state == _ModuleInfoInternal.STATE_PEER_REJECT:
					pass
				elif moi.state == _ModuleInfoInternal.STATE_EXCEPT:
					pass
				elif moi.state == _ModuleInfoInternal.STATE_PEER_EXCEPT:
					pass
				else:
					assert False

		logging.debug("SnLocalManager.onPeerChange: End")
		return

	def onPeerRemove(self, peerName):
		logging.debug("SnLocalManager.onPeerRemove: Start, %s", peerName)

		for moi in self.moduleObjDict[peerName]:
			if moi.state == _ModuleInfoInternal.STATE_INIT:
				pass
			elif moi.state == _ModuleInfoInternal.STATE_ACTIVE:
				self._moiChangeState(moi, _ModuleInfoInternal.STATE_INACTIVE)
				self._moiCallFunc(moi, "onInactive")
			elif moi.state == _ModuleInfoInternal.STATE_INACTIVE:
				pass
			elif moi.state == _ModuleInfoInternal.STATE_REJECT:
				self._moiChangeState(moi, _ModuleInfoInternal.STATE_INACTIVE)
			elif moi.state == _ModuleInfoInternal.STATE_PEER_REJECT:
				self._moiChangeState(moi, _ModuleInfoInternal.STATE_INACTIVE)
			elif moi.state == _ModuleInfoInternal.STATE_EXCEPT:
				pass
			elif moi.state == _ModuleInfoInternal.STATE_PEER_EXCEPT:
				self._moiChangeState(moi, _ModuleInfoInternal.STATE_INACTIVE)
			else:
				assert False

		logging.debug("SnLocalManager.onPeerRemove: End")
		return

	def onPeerSockRecv(self, peerName, userName, srcModuleName, data):
		moi = self._getMoiMapped(peerName, userName, srcModuleName)
		if moi.state != _ModuleInfoInternal.STATE_ACTIVE:
			return

		if self._typeCheck(data, SnDataPacketReject):
			self._moiChangeState(moi, _ModuleInfoInternal.STATE_PEER_REJECT)
			self._moiCallFunc(moi, "onInactive")
		elif self._typeCheck(data, SnDataPacketExcept):
			self._moiChangeState(moi, _ModuleInfoInternal.STATE_PEER_EXCEPT)
			self._moiCallFunc(moi, "onInactive")
		else:
			self._moiCallFunc(moi, "onRecv", data)

	def onLocalSockRecv(self, peerName, userName, moduleName, packetObj):
		moi = self._getMoi(peerName, userName, moduleName)
		if self._typeCheck(packetObj, _LoSockSendObj):
			self._sendObject(packetObj.peerName, packetObj.userName, packetObj.moduleName, packetObj.dataObj)
		elif self._typeCheck(packetObj, _LoSockRetn):
			self._moiCallFuncReturn(moi, packetObj.retVal)
		elif self._typeCheck(packetObj, _LoSockExcp):
			self._moiCallFuncExcept(packetObj, packetObj.excObj, packetObj.excInfo)
		else:
			assert False

	def onBeforeSleep(self, sleepType):
		pass

	def onAfterResume(self, sleepType):
		pass

	##### implementation ####

	def _sendObject(self, peerName, userName, moduleName, obj):
		if peerName == socket.gethostname():
			GLib.idle_add(self._idleLocalPeerRecv, peerName, userName, moduleName, obj)
		else:
			self.param.peerManager._sendDataObject(peerName, userName, moduleName, obj)

	def _sendReject(self, peerName, userName, moduleName, rejectMessage):
		logging.warning("SnLocalManager.sendReject, %s, %s, %s, %s", peerName, userName, moduleName, rejectMessage)

		messageObj = SnDataPacketReject()
		messageObj.message = rejectMessage
		if peerName == socket.gethostname():
			GLib.idle_add(self._idleLocalPeerRecv, peerName, userName, moduleName, messageObj)
		else:
			self.param.peerManager._sendDataObject(peerName, userName, moduleName, messageObj)

	def _sendExcept(self, peerName, userName, moduleName):
		logging.warning("SnLocalManager.sendExcept, %s, %s, %s", peerName, userName, moduleName)

		messageObj = SnDataPacketExcept()
		if peerName == socket.gethostname():
			GLib.idle_add(self._idleLocalPeerRecv, peerName, userName, moduleName, messageObj)
		else:
			self.param.peerManager._sendDataObject(peerName, userName, moduleName, messageObj)

	def _idleLocalPeerActive(self):
		self.onPeerChange(socket.gethostname(), self.localInfo)
		return False

	def _idleLocalPeerRecv(self, peerName, userName, moduleName, data):
		self.onPeerSockRecv(peerName, userName, moduleName, data)
		return False

	def _getLocalInfo(self):
		pgs = strict_pgs.PasswdGroupShadow("/")
		ret = SnSysInfo()

		ret.userList = []
		for uname in pgs.getNormalUserList():
			if uname in self.param.configManager.getUserBlackList():
				continue
			n = SnSysInfoUser()
			n.userName = uname
			ret.userList.append(n)

		ret.moduleList = []
		for mname in self.param.configManager.getModuleNameList():
			mInfo = self.param.configManager.getModuleInfo(mname)
			if mInfo.moduleScope == "sys":
				n = SnSysInfoModule()
				n.moduleName = mname
				n.userName = None
				ret.moduleList.append(n)
			elif mInfo.moduleScope == "usr":
				for uname in pgs.getNormalUserList():
					if uname in self.param.configManager.getUserBlackList():
						continue
					n = SnSysInfoModule()
					n.moduleName = mname
					n.userName = uname
					ret.moduleList.append(n)
			else:
				assert False

		return ret

	def _getModuleObjDict(self):
		"""Create a full module object collection"""

		pgs = strict_pgs.PasswdGroupShadow("/")
		ret = dict()

		# create self.moduleObjDict
		for pname in self.param.configManager.getHostNameList():
			moiList = []
			for mname in self.param.configManager.getModuleNameList():
				minfo = self.param.configManager.getModuleInfo(mname)
				if pname == socket.gethostname() and not minfo.moduleObj.getPropDict()["allow-local-peer"]:
					continue
				if minfo.moduleScope == "sys":
					moi = _ModuleInfoInternal()
					moi.peerName = pname
					moi.userName = None
					moi.moduleName = mname
					moi.moduleScope = minfo.moduleScope
					moi.moduleType = minfo.moduleType
					moi.moduleId = minfo.moduleId
					moi.propDict = minfo.moduleObj.getPropDict()
					moi.tmpDir = os.path.join(self.param.tmpDir, mname)
					moiList.append(moi)
				elif minfo.moduleScope == "usr":
					for uname in pgs.getNormalUserList():
						if uname in self.param.configManager.getUserBlackList():
							continue
						moi = _ModuleInfoInternal()
						moi.peerName = pname
						moi.userName = uname
						moi.moduleName = mname
						moi.moduleScope = minfo.moduleScope
						moi.moduleType = minfo.moduleType
						moi.moduleId = minfo.moduleId
						moi.propDict = minfo.moduleObj.getPropDict()
						moi.tmpDir = os.path.join(self.param.tmpDir, "%s-%s"%(mname, uname)
						moiList.append(moi)
				else:
					assert False
			ret[pname] = moiList

		# create SnModuleInstance
		for moiList in ret.values():
			for moi in moiList:
				if not moi.propDict["standalone"]:
					exec("from %s import ModuleInstanceObject"%(moi.moduleName.replace("-", "_")))
					moi.mo = ModuleInstanceObject(self, moi.peerName, moi.userName, moi.moduleName, moi.tmpDir)
				else:
					moi.proc = SnSubProcess(moi.peerName, moi.userName, moi.moduleName, moi.tmpDir, self.onLocalSockRecv, None)
					moi.proc.start()
				moi.state = _ModuleInfoInternal.STATE_INIT
				moi.failMessage = ""
				self._moiCallFunc(moi, "onInit")

		return ret

	def _getMoi(self, peerName, userName, moduleName):
		for moi in self.moduleObjDict[peerName]:
			if moi.userName == userName and moi.moduleName == moduleName:
				return moi
		assert False

	def _getMoiMapped(self, peerName, userName, srcModuleName):
		moi = self._findMoiMapped(peerName, userName, srcModuleName)
		assert moi is not None
		return moi

	def _findMoiMapped(self, peerName, userName, srcModuleName):
		for moi in self.moduleObjDict[peerName]:
			if moi.userName == userName and moi.moduleName == self._mapModuleName(srcModuleName):
				return moi
		return None

	def _matchMoiMapped(self, peerName, userName, srcModuleName):
		moi = self._findMoiMapped(peerName, userName, srcModuleName)
		return moi is not None

	def _matchPmi(self, peerName, peerInfo, moi):
		"""pmi stands for peer-module-info"""

		for pmi in peerInfo.moduleList:
			if (moi.peerName == peerName and moi.userName == pmi.userName
					and moi.moduleName == self._mapModuleName(pmi.moduleName)):
				return True
		return False

	def _mapModuleName(self, moduleName):
		strList = moduleName.split("-")
		if strList[1] == "server":
			strList[1] = "client"
		elif strList[1] == "client":
			strList[1] = "server"
		return "-".join(strList)

	def _typeCheck(self, obj, typeobj):
		return str(obj.__class__) == str(typeobj)

	def _moiChangeState(self, moi, newState, failMessage=""):
		if newState in [ _ModuleInfoInternal.STATE_REJECT, _ModuleInfoInternal.STATE_PEER_REJECT, _ModuleInfoInternal.STATE_EXCEPT ]:
			assert failMessage != ""
		else:
			assert failMessage == ""

		logging.info("SnLocalManager.moiChangeState: %s -> %s, %s", _module_state_to_str(moi.state), 
				_module_state_to_str(newState), _dbgmsg_moi_key(moi))
		moi.state = newState
		moi.failMessage = failMessage

	def _moiCallFunc(self, moi, funcName, *args):
		assert moi.calling is None

		logging.debug("SnLocalManager.moiCallFunc: call, %s, %s", _dbgmsg_moi_key(moi), funcName)
		moi.calling = funcName

		if moi.mo is not None:
			GLib.idle_add(self._idleMoiCallFuncImpl, moi, args)
		elif moi.proc is not None:
			p = _LoSockCall()
			p.funcName = funcName
			p.funcArgs = args
			moi.proc.get_pipe().send(p)
		else:
			assert False

	def _idleMoiCallFuncImpl(self, moi, *args):
		ret = None
		try:
			ret = exec("SnUtil.euidInvoke(moi.userName, moi.mo.%s, args)"%(moi.calling))
		except Exception as e:
			self._moiCallFuncExcept(moi, e, traceback.exc_info())
			return
		shutil.rmtree(moi.tmpDir, True)
		self._moiCallFuncReturn(moi, ret)

	def _moiCallFuncReturn(self, moi, retVal):
		funcName = moi.calling
		logging.debug("SnLocalManager.moiCallFunc: return, %s, %s", _dbgmsg_moi_key(moi), moi.calling)
		moi.calling = None

		if funcName == "onInit":
			peerInfo = self.param.peerManager.getPeerInfo(moi.peerName)
			if peerInfo is not None and self._matchPmi(moi.peerName, self.param.peerManager.getPeerInfo(moi.peerName), moi):
				self._moiChangeState(moi, _ModuleInfoInternal.STATE_ACTIVE)
				self._moiCallFunc(moi, "onActive")
			else:
				self._moiChangeState(moi, _ModuleInfoInternal.STATE_INACTIVE)
		elif funcName == "onInactive":
			if moi.state == _ModuleInfoInternal.STATE_REJECT:
				self._sendReject(moi.peerName, moi.userName, moi.moduleName, moi.failMessage)
		elif funcName == "onActive":
			pass
		elif funcName == "onRecv":
			pass
		else:
			assert False
		
	def _moiCallFuncExcept(self, moi, excObj, excInfo):
		funcName = moi.calling
		logging.debug("SnLocalManager.moiCallFunc: except, %s, %s, %s, %s", _dbgmsg_moi_key(moi),
				moi.calling, excObj.__class__, excObj)
		moi.calling = None

		if funcName == "onInit":
			self._moiChangeState(moi, _ModuleInfoInternal.STATE_EXCEPT, excInfo)
		elif funcName == "onInactive":
			self._moiChangeState(moi, _ModuleInfoInternal.STATE_EXCEPT, excInfo)
			if moi.state == _ModuleInfoInternal.STATE_REJECT:
				self._sendExcept(moi.peerName, moi.userName, moi.moduleName)
		elif funcName == "onActive":
			self._moiChangeState(moi, _ModuleInfoInternal.STATE_EXCEPT, excInfo)
			self._sendExcept(moi.peerName, moi.userName, moi.moduleName)
		elif funcName == "onRecv":
			if _typeCheck(excObj, SnRejectException):
				self._moiChangeState(moi, _ModuleInfoInternal.STATE_REJECT, excObj.message)
				self._moiCallFunc(moi, "onInactive")
			else:
				self._moiChangeState(moi, _ModuleInfoInternal.STATE_EXCEPT, excInfo)
				self._sendExcept(moi.peerName, moi.userName, moi.moduleName)
		else:
			assert False

class _ModuleInfoInternal:
	STATE_INIT = 0
	STATE_INACTIVE = 1
	STATE_ACTIVE = 2
	STATE_REJECT = 3
	STATE_PEER_REJECT = 4
	STATE_EXCEPT = 5
	STATE_PEER_EXCEPT = 6

	WORK_STATE_IDLE = 0
	WORK_STATE_WORKING = 1

	peerName = None							# str
	userName = None							# str, can be None
	moduleName = None						# str, "sys-server-name"
	moduleScope = None						# str, "sys" "usr"
	moduleType = None						# str, "server" "client" "peer"
	moduleId = None							# str
	propDict = None							# dict
	tmpDir = None							# str
	mo = None								# obj, SnModuleInstance, standalone module: None
	proc = None								# obj, not-standalone module: None
	state = None							# enum
	failMessage = None						# str
	calling = None							# str
	workState = None						# enum

class _LoSockSendObj:
	peerName = None							# str
	userName = None							# str
	moduleName = None						# str
	dataObj = None							# obj

class _LoSockCall:
	funcName = None							# str
	funcArgs = None							# list<obj>

class _LoSockRetn:
	retVal = None							# obj, None means no return value

class _LoSockExcp:
	excObj = None							# str
	excInfo = None							# str

def _dbgmsg_moi_key(moi):
	if moi.userName is None:
		return "%s, %s"%(moi.peerName, moi.moduleName)
	else:
		return "%s, %s, %s"%(moi.peerName, moi.userName, moi.moduleName)

def _module_state_to_str(moduleState):
	if moduleState == _ModuleInfoInternal.STATE_INIT:
		return "STATE_INIT"
	elif moduleState == _ModuleInfoInternal.STATE_INACTIVE:
		return "STATE_INACTIVE"
	elif moduleState == _ModuleInfoInternal.STATE_ACTIVE:
		return "STATE_ACTIVE"
	elif moduleState == _ModuleInfoInternal.STATE_REJECT:
		return "STATE_REJECT"
	elif moduleState == _ModuleInfoInternal.STATE_PEER_REJECT:
		return "STATE_PEER_REJECT"
	elif moduleState == _ModuleInfoInternal.STATE_EXCEPT:
		return "STATE_EXCEPT"
	elif moduleState == _ModuleInfoInternal.STATE_PEER_EXCEPT:
		return "STATE_PEER_EXCEPT"
	else:
		assert False

