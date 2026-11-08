import pathlib,re,shutil,subprocess,json
P=pathlib.Path
spec={'interface': 'wlxfc221c300e99', 'mac': 'fc:22:1c:30:0e:99', 'usb_path': '/sys/devices/platform/bus@0/3610000.usb/usb2/2-1/2-1.3/2-1.3:1.0', 'source_host': 'fl01'}
cards=[c for c in P('/sys/class/net').iterdir() if (c/'device/driver').resolve().name=='rtl88xxau_wfb']
assert len(cards)==1
assert cards[0].name==spec['interface'] and (cards[0]/'address').read_text().strip()==spec['mac']
target=P('/etc/systemd/system/wfb-fl-client-daemon.service.d/zzzzz-heartbeat-release.conf')
old='[Service]\nExecStart=\nExecStart=/usr/bin/python3 -I -B /srv/wfb-ng-fl/releases/heartbeat-c6659488f649-20261108T064511Z/daemon.py client --config /etc/wfb-ng-fl/node.json --work-dir /srv/wfb-ng-fl/client --channel 149 --bandwidth-mhz 40 --radio-txpower-dbm 18 --uplink-mcs 4 --interface wlxfc221c300cbc\nWorkingDirectory=/\nUnsetEnvironment=PYTHONPATH PYTHONHOME\nKillMode=control-group\n'
assert target.read_text()==old
new,n=re.subn(r'--interface \S+', '--interface '+spec['interface'],old)
assert n==1
backup=target.with_name(target.name+'.card-swap-20261108T070828Z.bak')
assert not backup.exists()
shutil.copy2(target,backup)
target.write_text(new)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','restart','wfb-fl-client-daemon.service'],check=True,timeout=35)
print(json.dumps({'backup':str(backup),'old':old,'new':new,'restart':'success'}))
