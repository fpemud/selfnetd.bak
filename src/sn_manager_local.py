#!/usr/bin/python2
# -*- coding: utf-8; tab-width: 4; indent-tabs-mode: t -*-

import logging
import strict_pgs
from gi.repository import GLib

from sn_manager_peer import SnPeerInfo
from sn_manager_peer import SnPeerInfoUser
from sn_manager_peer import SnPeerInfoModule
from sn_module import SnModuleInstance

# fixme: needs to consider user change

class SnDataPacket:
	srcUserName = None				# str, can be None
	srcModuleName = None			# str
	data = None						# object

class SnDataPacketReject:
	message = None					# str

class SnLocalManager:

	def __init__(self, param):
		logging.debug("SnLocalManager.__init__: Start")

		self.param = param
		self.localInfo = self._getLocalInfo()			# SnPeerInfo
		self.moduleObjDict = self._getModuleObjDict()

		logging.debug("SnLocalManager.__init__: End")
		return

	def dispose(self):
		logging.debug("SnLocalManager.dispose: Start")

		for peerName, moduleObjList in self.moduleObjDict.items():
			for mo in moduleObjList:
				assert mo.getState() == SnModuleInstance.STATE_INACTIVE

		logging.debug("SnLocalManager.dispose: End")
		return

	def getLocalInfo(self):
		return self.localInfo

	##### event callback ####

	def onPeerChange(self, peerName):
		logging.debug("SnLocalManager.onPeerChange: Start, %s", peerName)

		peerInfo = self.param.peerManager.getPeerInfo(peerName)

		# process module inactive
		for mo in self.moduleObjDict[peerName]:
			if not self._matchPeerModuleList(peerInfo, mo):
				if mo.getState() == SnModuleInstance.STATE_ACTIVE:
					logging.debug("SnLocalManager.onPeerChange: mo active -> inactive start, %s, %s, %s", peerName, mo.getUserName(), mo.getModuleName())
					mo.onInactive()
					mo.setState(SnModuleInstance.STATE_INACTIVE)
					logging.debug("SnLocalManager.onPeerChange: mo active -> inactive end")
				elif mo.getState() == SnModuleInstance.STATE_INACTIVE:
					pass
				elif mo.getState() == SnModuleInstance.STATE_REJECT:
					logging.debug("SnLocalManager.onPeerChange: mo reject -> inactive start, %s, %s, %s", peerName, mo.getUserName(), mo.getModuleName())
					mo.setState(SnModuleInstance.STATE_INACTIVE)
					logging.debug("SnLocalManager.onPeerChange: mo reject -> inactive end")
				else:
					assert False
			

		# process module active
		for mio in peerInfo.moduleList:
			mo = self._findModuleList(self.moduleObjDict[peerName], mio)
			if mo is not None:
				if mo.getState() == SnModuleInstance.STATE_ACTIVE:
					pass
				elif mo.getState() == SnModuleInstance.STATE_INACTIVE:
					logging.debug("SnLocalManager.onPeerChange: mo inactive -> active start, %s, %s, %s", peerName, mo.getUserName(), mo.getModuleName())
					mo.onActive()
					mo.setState(SnModuleInstance.STATE_ACTIVE)
					logging.debug("SnLocalManager.onPeerChange: mo inactive -> active end")
				elif mo.getState() == SnModuleInstance.STATE_REJECT:
					pass
				else:
					assert False

		logging.debug("SnLocalManager.onPeerChange: End")
		return

	def onPeerRemove(self, peerName):
		logging.debug("SnLocalManager.onPeerRemove: Start, %s", peerName)

		for mo in self.moduleObjDict[peerName]:
			if mo.getState() == SnModuleInstance.STATE_ACTIVE:
				logging.debug("SnLocalManager.onPeerRemove: mo active -> inactive start, %s, %s, %s", peerName, mo.getUserName(), mo.getModuleName())
				mo.onInactive()
				mo.setState(SnModuleInstance.STATE_INACTIVE)
				logging.debug("SnLocalManager.onPeerRemove: mo active -> inactive end")
			elif mo.getState() == SnModuleInstance.STATE_INACTIVE:
				pass
			elif mo.getState() == SnModuleInstance.STATE_REJECT:
				logging.debug("SnLocalManager.onPeerRemove: mo reject -> inactive start, %s, %s, %s", peerName, mo.getUserName(), mo.getModuleName())
				mo.setState(SnModuleInstance.STATE_INACTIVE)
				logging.debug("SnLocalManager.onPeerRemove: mo reject -> inactive end")
			else:
				assert False

		logging.debug("SnLocalManager.onPeerRemove: End")
		return

	def onPacketRecv(self, peerName, userName, srcModuleName, data):
		logging.debug("SnLocalManager.onPacketRecv: Start, %s, %s, %s", peerName, userName, srcModuleName)

		moduleName = self._getModuleNameByPeerModuleName(srcModuleName)
		for mo in self.moduleObjDict[peerName]:
			if mo.getUserName() == userName and mo.getModuleName() == moduleName:
				if self._typeCheck(data, SnDataPacketReject):
					mo.onReject(data.message)
					mo.onInactive()
					mo.setState(SnModuleInstance.STATE_REJECT)
				else:
					mo.onRecv(data)
				logging.debug("SnLocalManager.onPacketRecv: End")
				return
		assert False

	##### implementation ####

	def _sendObject(self, peerName, userName, moduleName, obj):
		self.param.peerManager._sendDataObject(peerName, userName, moduleName, obj)

	def _sendReject(self, peerName, userName, moduleName, rejectMessage):
		# record to log
		logging.warning("send reject, module closing gracefully, %s, %s, %s, %s", peerName, userName, moduleName, rejectMessage)

		# send reject message
		rejectObj = SnDataPacketReject()
		rejectObj.message = rejectMessage
		self.param.peerManager._sendDataObject(peerName, userName, moduleName, rejectObj)

		# add module graceful close callback
		GLib.idle_add(self._gcComplete, peerName, userName, moduleName)

	def _gcComplete(self, peerName, userName, moduleName):
		logging.warning("module graceful close complete, %s, %s, %s", peerName, userName, moduleName)

		for mo in self.moduleObjDict[peerName]:
			if mo.getUserName() == userName and mo.getModuleName() == moduleName:
				mo.onInactive()
				mo.setState(SnModuleInstance.STATE_REJECT)
				return False
		assert False

	def _getLocalInfo(self):
		pgs = strict_pgs.PasswdGroupShadow("/")
		ret = SnPeerInfo()

		ret.userList = []
		for uname in pgs.getUserList():
			if uname in self.param.configManager.getUserBlackList():
				continue
			n = SnPeerInfoUser()
			n.userName = uname
			ret.userList.append(n)

		ret.moduleList = []
		for mname in self.param.configManager.getModuleNameList():
			mInfo = self.param.configManager.getModuleInfo(mname)
			if mInfo.moduleScope == "sys":
				n = SnPeerInfoModule()
				n.moduleName = mname
				n.userName = None
				ret.moduleList.append(n)
			elif mInfo.moduleScope == "usr":
				for uname in pgs.getUserList():
					if uname in self.param.configManager.getUserBlackList():
						continue
					n = SnPeerInfoModule()
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

		# create self.moduleObjDict, invoke SnModuleInstance.onInit
		for pname in self.param.configManager.getHostNameList():
			moduleObjList = []
			for mname in self.param.configManager.getModuleNameList():
				minfo = self.param.configManager.getModuleInfo(mname)
				exec("from %s import ModuleInstanceObject"%(mname.replace("-", "_")))
				if minfo.moduleScope == "sys":
					mo = ModuleInstanceObject(self, minfo.moduleObj, minfo.moduleParamDict, pname, None)
					mo.onInit()
					mo.setState(SnModuleInstance.STATE_INACTIVE)
					moduleObjList.append(mo)
				elif minfo.moduleScope == "usr":
					for uname in pgs.getUserList():
						if uname in self.param.configManager.getUserBlackList():
							continue
						mo = ModuleInstanceObject(self, minfo.moduleObj, minfo.moduleParamDict, pname, uname)
						mo.onInit()
						mo.setState(SnModuleInstance.STATE_INACTIVE)
						moduleObjList.append(mo)
				else:
					assert False
			ret[pname] = moduleObjList

		return ret

	def _matchPeerModuleList(self, peerInfo, mo):
		for mio in peerInfo.moduleList:
			if mo.getUserName() == mio.userName and mo.getModuleName() == self._getModuleNameByPeerModuleName(mio.moduleName):
				return True
		return False

	def _findModuleList(self, moduleObjList, mio):
		for mo in moduleObjList:
			if mo.getUserName() == mio.userName and mo.getModuleName() == self._getModuleNameByPeerModuleName(mio.moduleName):
				return mo
		return None

	def _getModuleNameByPeerModuleName(self, moduleName):
		strList = moduleName.split("-")
		if strList[1] == "server":
			strList[1] = "client"
		elif strList[1] == "client":
			strList[1] = "server"
		return "-".join(strList)

	def _typeCheck(self, obj, typeobj):
		return str(obj.__class__) == str(typeobj)

