#!/usr/bin/python2
# -*- coding: utf-8; tab-width: 4; indent-tabs-mode: t -*-

import os
import shutil
import dbus
import dbus.service
from gi.repository import GObject
from sn_util import SnUtil
from sn_manager_local import SnLocalManager
from sn_manager_peer import SnPeerManager

################################################################################
# DBus API Docs
################################################################################
#
# ==== Main Application ====
# Service               org.fpemud.SelfNet
# Interface             org.fpemud.SelfNet
# Object path           /
#
# Methods:
# str               GetWorkState()
# array<peerId:int> GetPeerList()
# peerId:int        GetPeerByName(peerName:str)
#
# Signals:
# WorkStateChanged(newWorkState:str)
#
# ==== Peer ====
# Service               org.fpemud.SelfNet
# Interface             org.fpemud.SelfNet.Peer
# Object path           /Peers/{peerId:int}
#
# Methods:
# str               GetName()
# str               GetPowerState()
# void              DoPowerOperation(opName:str)
# 
# Signals:
# PowerStateChanged(newPowerState:str)
#

class DbusMainObject(dbus.service.Object):

	def __init__(self, param):
		self.param = param
		self.peerList = []

		# initialize peer list
		i = 0
		for pn in self.param.peerManager.getPeerNameList():
			po = DbusPeerObject(self.param, i, pn)
			self.peerList.append(po)
			i = i + 1

		# register dbus object path
		bus_name = dbus.service.BusName('org.fpemud.SelfNet', bus=dbus.SystemBus())
		dbus.service.Object.__init__(self, bus_name, '/org/fpemud/SelfNet')

	def release(self):
		self.remove_from_connection()

	@dbus.service.method('org.fpemud.SelfNet', in_signature='', out_signature='s')
	def GetWorkState(self):
		ws = self.param.localManager.getWorkState()
		if ws == SnLocalManager.WORK_STATE_IDLE:
			return "idle"
		elif ws == SnLocalManager.WORK_STATE_WORKING:
			return "working"
		else:
			assert False

	@dbus.service.method('org.fpemud.SelfNet', in_signature='', out_signature='ai')
	def GetPeerList(self):
		ret = []
		for po in self.peerList:
			ret.append(po.peerId)
		return ret

	@dbus.service.method('org.fpemud.SelfNet', in_signature='s', out_signature='i')
	def GetPeerByName(self, peerName):
		for po in self.peerList:
			if peerName == po.peerName:
				return po.peerId
		return -1

	@dbus.service.signal(dbus_interface='org.fpemud.SelfNet', signature='s')
	def WorkStateChanged(self, newWorkState):
		pass

class DbusPeerObject(dbus.service.Object):

	def __init__(self, param, peerId, peerName):
		self.param = param
		self.peerId = peerId
		self.peerName = peerName

		# register dbus object path
		bus_name = dbus.service.BusName('org.fpemud.SelfNet', bus=dbus.SystemBus())
		dbus.service.Object.__init__(self, bus_name, '/org/fpemud/SelfNet/Peer/%d'%(self.peerId))

	def release(self):
		self.remove_from_connection()

	@dbus.service.method('org.fpemud.SelfNet.Peer', sender_keyword='sender',
	                     in_signature='', out_signature='s')
	def GetName(self, sender=None):
		return self.peerName
	                     
	@dbus.service.method('org.fpemud.SelfNet.Peer', sender_keyword='sender',
	                     in_signature='', out_signature='s')
	def GetPowerState(self, sender=None):
		powerStateDict = {
			SnPeerManager.POWER_STATE_UNKNOWN: "unknown",
			SnPeerManager.POWER_STATE_RUNNING: "running",
			SnPeerManager.POWER_STATE_POWEROFF: "poweroff",
			SnPeerManager.POWER_STATE_RESTARTING: "restarting",
			SnPeerManager.POWER_STATE_SUSPEND: "suspend",
			SnPeerManager.POWER_STATE_HIBERNATE: "hibernate",
			SnPeerManager.POWER_STATE_HYBRID_SLEEP: "hybrid-sleep"
		}
		powerState = self.param.peerManager.getPeerPowerState(self.peerName)
		return powerStateDict[powerState]

	@dbus.service.method('org.fpemud.SelfNet.Peer', sender_keyword='sender',
	                     in_signature='s', out_signature='',
	                     async_callbacks=('reply_handler', 'error_handler'))
	def DoPowerOperation(self, opName, reply_handler, error_handler, sender=None):
		powerOpNameDict = {
			"poweron": SnPeerManager.POWER_OP_POWERON,
			"poweroff": SnPeerManager.POWER_OP_POWEROFF,
			"restart": SnPeerManager.POWER_OP_RESTART,
			"suspend": SnPeerManager.POWER_OP_SUSPEND,
			"hibernate": SnPeerManager.POWER_OP_HIBERNATE,
			"hybrid-sleep": SnPeerManager.POWER_OP_HYBRID_SLEEP,
		}
		try:
			self.param.peerManager.doPeerPowerOperationAsync(self.peerName,
				powerOpNameDict[opName], reply_handler, error_handler)
		except Exception as e:
			error_handler(e)

	@dbus.service.signal(dbus_interface='org.fpemud.SelfNet.Peer', signature='s')
	def PowerStateChanged(self, newPowerState):
		pass

