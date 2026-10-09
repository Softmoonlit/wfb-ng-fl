SHELL = /bin/bash
ARCH ?= $(shell uname -m)
PYTHON ?= /usr/bin/python3
OS_CODENAME ?= $(shell lsb_release -cs)

ifneq ("$(wildcard .git)","")
    RELEASE := $(or $(RELEASE),\
                    $(shell git rev-parse --abbrev-ref HEAD | grep -v '^stable$$'),\
                    $(shell git describe --all --match 'release-*' --match 'origin/release-*' --abbrev=0 HEAD 2>/dev/null | grep -o '[^/]*$$'),\
                    unknown)
    COMMIT := $(or $(COMMIT), $(shell git rev-parse HEAD))
    SOURCE_DATE_EPOCH := $(or $(SOURCE_DATE_EPOCH), $(shell git show -s --format="%ct" $(COMMIT)), $(shell date "+%s"))
    VERSION := $(or $(VERSION), $(shell $(PYTHON) ./version.py $(SOURCE_DATE_EPOCH) $(RELEASE)))
else
    COMMIT := $(or $(COMMIT), release)
    SOURCE_DATE_EPOCH := $(or $(SOURCE_DATE_EPOCH), $(shell date "+%s"))
    VERSION := $(or $(VERSION), $(shell basename $(PWD) | grep -E -o '[0-9]+.[0-9]+(.[0-9]+)?$$'), 0.0.0)
endif

ENV ?= $(CURDIR)/env
STDEB ?= "git+https://github.com/svpcom/stdeb"

export VERSION COMMIT SOURCE_DATE_EPOCH

_LDFLAGS := $(LDFLAGS) -lrt -lsodium
_CFLAGS := $(CFLAGS) -Wall -O2 -fno-strict-aliasing -DZFEX_UNROLL_ADDMUL_SIMD=8 -DZFEX_USE_INTEL_SSSE3 -DZFEX_USE_ARM_NEON -DZFEX_INLINE_ADDMUL -DZFEX_INLINE_ADDMUL_SIMD -DWFB_VERSION='"$(VERSION)-$(shell /bin/bash -c '_tmp=$(COMMIT); echo $${_tmp::8}')"'

.PHONY: all build_v6 version all_bin kcp_tools install_v8 acceptance_v6_realhw rpm deb bdist check pylint clean

all: build_v6

build_v6: wfb_v6_uplink

version:
	@echo -e "RELEASE=$(RELEASE)\nCOMMIT=$(COMMIT)\nVERSION=$(VERSION)\nSOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH)"

$(ENV):
	$(PYTHON) -m venv --clear $(ENV)
	$$(PATH="$(ENV)/bin:$(ENV)/local/bin:$(PATH)" which python3) -m pip install --upgrade pip setuptools $(STDEB)

all_bin: wfb_rx wfb_tx wfb_keygen wfb_tx_cmd wfb_tun wfb_token_scheduler wfb_v6_uplink

gs.key: wfb_keygen
	@if ! [ -f gs.key ]; then ./wfb_keygen; fi

src/%.o: src/%.c src/*.h
	$(CC) $(_CFLAGS) -std=gnu99 -c -o $@ $<

src/%.o: src/%.cpp src/*.hpp src/*.h
	$(CXX) $(_CFLAGS) -std=gnu++11 -c -o $@ $<

# 正式底座复用收发实现，编译为无独立 main 的共享对象。
src/rx.link.o: src/rx.cpp src/*.hpp src/*.h
	$(CXX) $(_CFLAGS) -std=gnu++11 -D__WFB_RX_SHARED_LIBRARY__ -c -o $@ $<

src/tx.link.o: src/tx.cpp src/*.hpp src/*.h
	$(CXX) $(_CFLAGS) -std=gnu++11 -D__WFB_TX_SHARED_LIBRARY__ -c -o $@ $<

wfb_rx: src/rx.o src/radiotap.o src/zfex.o src/wifibroadcast.o src/control_envelope.o src/token_event_ipc.o src/token_authorization_ipc.o
	$(CXX) -o $@ $^ $(_LDFLAGS) -lpcap

wfb_tx: src/tx.o src/zfex.o src/wifibroadcast.o src/token_authorization.o src/token_authorization_ipc.o src/tx_token_gate.o
	$(CXX) -o $@ $^ $(_LDFLAGS)

wfb_token_namespace_bridge: src/main_token_namespace_bridge.o src/token_namespace_bridge.o src/token_authorization_ipc.o src/token_event_ipc.o src/wifibroadcast.o
	$(CXX) -o $@ $^ $(LDFLAGS)

wfb_keygen: src/keygen.o
	$(CC) -o $@ $^ $(_LDFLAGS)

wfb_tx_cmd: src/tx_cmd.o
	$(CC) -o $@ $^ $(LDFLAGS)

wfb_tun: src/wfb_tun.o
	$(CC) -o $@ $^ $(LDFLAGS) -levent_core

wfb_token_scheduler: src/main_token_scheduler.o src/token_scheduler.o src/token_authorization_ipc.o src/wifibroadcast.o
	$(CXX) -o $@ $^ $(LDFLAGS)

wfb_v6_uplink: src/v6_uplink.o src/v6_plaintext_fec_tx.o src/tx.link.o src/rx.link.o src/radiotap.o src/zfex.o src/wifibroadcast.o src/control_envelope.o src/token_scheduler.o src/token_authorization.o src/token_authorization_ipc.o src/token_event_ipc.o src/tx_token_gate.o
	$(CXX) -o $@ $^ $(_LDFLAGS) -lpcap

kcp_tools: kcp_small_sender kcp_small_receiver

kcp_small_sender: src/kcp_small_sender.o src/ikcp.o
	$(CXX) -o $@ $^ $(LDFLAGS) -lcrypto

kcp_small_receiver: src/kcp_small_receiver.o src/ikcp.o
	$(CXX) -o $@ $^ $(LDFLAGS) -lcrypto

wfb_rtsp: src/rtsp_server.c
	$(CC) $(_CFLAGS) $(shell pkg-config --cflags gstreamer-rtsp-server-1.0) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs gstreamer-rtsp-server-1.0)

install_v8: build_v6
	./scripts/install/install-v8.sh

acceptance_v6_realhw:
	@echo "手动上行链路验收：tests/link_uplink/v6新底座无SSH手动上行演示手册.md"
	@echo "请按手册执行 tests/link_uplink/v6_manual_uplink_demo.sh。"

rpm: build_v6 $(ENV)
	rm -rf dist
	$$(PATH="$(ENV)/bin:$(ENV)/local/bin:$(PATH)" which python3) ./setup.py bdist_rpm --force-arch $(ARCH) --requires python3-twisted,python3-pyroute2,python3-pyserial,python3-msgpack,python3-jinja2,python3-yaml,socat,iw,uftp,iproute,libsodium,libpcap
	rm -rf wfb_ng.egg-info/

deb: build_v6 $(ENV)
	rm -rf deb_dist
	$$(PATH="$(ENV)/bin:$(ENV)/local/bin:$(PATH)" which python3) ./setup.py --command-packages=stdeb.command sdist_dsc --debian-version 0~$(OS_CODENAME) bdist_deb
	rm -rf wfb_ng.egg-info/ wfb-ng-$(VERSION).tar.gz

bdist: build_v6 $(ENV)
	rm -rf dist
	$$(PATH="$(ENV)/bin:$(ENV)/local/bin:$(PATH)" which python3) ./setup.py bdist --plat-name linux-$(ARCH)
	rm -rf wfb_ng.egg-info/

check:
	cppcheck --force --std=c++11 --library=std --library=posix --library=gnu --inline-suppr --template=gcc --enable=all --suppress=cstyleCast --suppress=missingOverride --suppress=missingIncludeSystem src/
	$(MAKE) clean
	$(MAKE) CFLAGS="$(CFLAGS) -g -fno-omit-frame-pointer -fsanitize=address -fsanitize=undefined -fsanitize=pointer-compare -fsanitize=pointer-subtract -fsanitize=leak -fsanitize-address-use-after-scope" LDFLAGS="-static-libasan -fsanitize=address -fsanitize=undefined -fsanitize=leak" build_v6
	$(MAKE) clean

pylint:
	pylint --disable=R,C wfb_ng/*.py

clean:
	rm -rf env wfb_rx wfb_tx wfb_tx_cmd wfb_tun wfb_token_scheduler wfb_token_namespace_bridge wfb_v6_uplink kcp_small_sender kcp_small_receiver wfb_rtsp wfb_keygen dist deb_dist build wfb_ng.egg-info/ wfb_ng-*.tar.gz *~ src/*.o
