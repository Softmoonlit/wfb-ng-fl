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

ENV ?= $(PWD)/env
DOCKER_ARCH ?= amd64
DOCKER_SRC_IMAGE ?= "p2ptech/cross-build:2023-02-21-raspios-bullseye-armhf-lite"
STDEB ?= "git+https://github.com/svpcom/stdeb"
QEMU_CPU ?= "max"

export VERSION COMMIT SOURCE_DATE_EPOCH

_LDFLAGS := $(LDFLAGS) -lrt -lsodium
_CFLAGS := $(CFLAGS) -Wall -O2 -fno-strict-aliasing -DZFEX_UNROLL_ADDMUL_SIMD=8 -DZFEX_USE_INTEL_SSSE3 -DZFEX_USE_ARM_NEON -DZFEX_INLINE_ADDMUL -DZFEX_INLINE_ADDMUL_SIMD -DWFB_VERSION='"$(VERSION)-$(shell /bin/bash -c '_tmp=$(COMMIT); echo $${_tmp::8}')"'

V6_DEFAULT_BIN := wfb_v6_uplink
V6_DEFAULT_TESTS := fec_test libsodium_test control_envelope_test v6_uplink_queue_test v6_uplink_downlink_nonce_test

all: build_v6 test_v6

build_v6: $(V6_DEFAULT_BIN)

version:
	@echo -e "RELEASE=$(RELEASE)\nCOMMIT=$(COMMIT)\nVERSION=$(VERSION)\nSOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH)"

$(ENV):
	$(PYTHON) -m venv --clear $(ENV)
	$$(PATH=$(ENV)/bin:$(ENV)/local/bin:$(PATH) which python3) -m pip install --upgrade pip setuptools $(STDEB)

all_bin: wfb_rx wfb_tx wfb_keygen wfb_tx_cmd wfb_tun wfb_token_scheduler wfb_v6_uplink

gs.key: wfb_keygen
	@if ! [ -f gs.key ]; then ./wfb_keygen; fi

src/%.o: src/%.c src/*.h
	$(CC) $(_CFLAGS) -std=gnu99 -c -o $@ $<

src/%.o: src/%.cpp src/*.hpp src/*.h
	$(CXX) $(_CFLAGS) -std=gnu++11 -c -o $@ $<


# Rules for tx_gate_test object files
src/radiotap.tx_gate_test.o: src/radiotap.c
	$(CC) $(_CFLAGS) -std=gnu99 -c -o $@ $<

src/zfex.tx_gate_test.o: src/zfex.c
	$(CC) $(_CFLAGS) -std=gnu99 -c -o $@ $<

src/%.tx_gate_test.o: src/%.cpp
	$(CXX) $(_CFLAGS) -std=gnu++11 -c -o $@ $<

# Rules for rx_test object files
src/radiotap.rx_test.o: src/radiotap.c
	$(CC) $(_CFLAGS) -std=gnu99 -c -o $@ $<

src/zfex.rx_test.o: src/zfex.c
	$(CC) $(_CFLAGS) -std=gnu99 -c -o $@ $<

src/rx.rx_test.o: src/rx.cpp
	$(CXX) $(_CFLAGS) -std=gnu++11 -D__WFB_RX_SHARED_LIBRARY__ -c -o $@ $<

src/%.rx_test.o: src/%.cpp
	$(CXX) $(_CFLAGS) -std=gnu++11 -c -o $@ $<

# Rules for v6_test object files
src/radiotap.v6_test.o: src/radiotap.c
	$(CC) $(_CFLAGS) -std=gnu99 -c -o $@ $<

src/zfex.v6_test.o: src/zfex.c
	$(CC) $(_CFLAGS) -std=gnu99 -c -o $@ $<

src/rx.v6_test.o: src/rx.cpp
	$(CXX) $(_CFLAGS) -std=gnu++11 -D__WFB_RX_SHARED_LIBRARY__ -c -o $@ $<

src/tx.v6_test.o: src/tx.cpp
	$(CXX) $(_CFLAGS) -std=gnu++11 -D__WFB_TX_SHARED_LIBRARY__ -c -o $@ $<

src/%.v6_test.o: src/%.cpp
	$(CXX) $(_CFLAGS) -std=gnu++11 -c -o $@ $<
wfb_rx: src/rx.o src/radiotap.o src/zfex.o src/wifibroadcast.o src/control_envelope.o src/token_event_ipc.o src/token_authorization_ipc.o
	$(CXX) -o $@ $^ $(_LDFLAGS) -lpcap

wfb_tx: src/tx.o src/zfex.o src/wifibroadcast.o src/token_authorization.o src/token_authorization_ipc.o src/tx_token_gate.o
	$(CXX) -o $@ $^ $(_LDFLAGS)

fec_test: src/fec_test.cpp src/zfex.o
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs catch2-with-main)

libsodium_test: src/libsodium_test.cpp
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) -lsodium $(shell pkg-config --libs catch2-with-main)

token_scheduler_test: src/token_scheduler_test.cpp src/token_scheduler.o src/token_authorization_ipc.o src/token_event_ipc.o src/wifibroadcast.o
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs catch2-with-main)

token_authorization_test: src/token_authorization_test.cpp src/token_authorization.o
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs catch2-with-main)

tx_token_gate_test: src/tx_token_gate_test.cpp src/tx_token_gate.o src/token_authorization.o
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs catch2-with-main)

token_authorization_ipc_test: src/token_authorization_ipc_test.cpp src/token_authorization_ipc.cpp src/token_event_ipc.cpp src/wifibroadcast.cpp
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs catch2-with-main)

token_namespace_bridge_test: src/token_namespace_bridge_test.cpp src/token_namespace_bridge.cpp src/token_authorization_ipc.cpp src/token_event_ipc.cpp src/wifibroadcast.cpp
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs catch2-with-main)

wfb_token_namespace_bridge: src/main_token_namespace_bridge.o src/token_namespace_bridge.o src/token_authorization_ipc.o src/token_event_ipc.o src/wifibroadcast.o
	$(CXX) -o $@ $^ $(LDFLAGS)

tx_authorization_integration_test: src/tx_authorization_integration_test.cpp src/token_authorization_ipc.cpp src/tx_token_gate.cpp src/token_authorization.cpp src/token_scheduler.cpp src/wifibroadcast.cpp
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs catch2-with-main)

tx_data_source_gate_test: src/tx_data_source_gate_test.cpp src/radiotap.tx_gate_test.o src/zfex.tx_gate_test.o src/wifibroadcast.tx_gate_test.o src/token_authorization.tx_gate_test.o src/token_authorization_ipc.tx_gate_test.o src/token_event_ipc.tx_gate_test.o src/tx_token_gate.tx_gate_test.o
	$(CXX) $(_CFLAGS) -o $@ $^ $(_LDFLAGS) -lpcap $(shell pkg-config --libs catch2-with-main)

: src/.cpp src/control_envelope.cpp
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs catch2-with-main)

control_envelope_test: src/control_envelope_test.cpp src/control_envelope.cpp
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs catch2-with-main)

rx_token_ipc_test: src/rx_token_ipc_test.cpp src/token_event_ipc.cpp src/wifibroadcast.cpp
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs catch2-with-main)

rx_token_listener_test: src/rx_token_listener_test.cpp src/rx.rx_test.o src/radiotap.rx_test.o src/zfex.rx_test.o src/wifibroadcast.rx_test.o src/control_envelope.rx_test.o src/token_event_ipc.rx_test.o src/token_authorization_ipc.rx_test.o
	$(CXX) $(_CFLAGS) -o $@ $^ $(_LDFLAGS) -lpcap $(shell pkg-config --libs catch2-with-main)

v6_uplink_queue_test: src/v6_uplink_queue_test.cpp
	$(CXX) $(_CFLAGS) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs catch2-with-main)

v6_uplink_downlink_nonce_test: src/v6_uplink_downlink_nonce_test.cpp src/v6_plaintext_fec_tx.o src/tx.v6_test.o src/rx.v6_test.o src/radiotap.v6_test.o src/zfex.v6_test.o src/wifibroadcast.v6_test.o src/control_envelope.v6_test.o src/token_scheduler.v6_test.o src/token_authorization.v6_test.o src/token_authorization_ipc.v6_test.o src/token_event_ipc.v6_test.o src/tx_token_gate.o
	$(CXX) $(_CFLAGS) -o $@ $^ $(_LDFLAGS) -lpcap $(shell pkg-config --libs catch2-with-main)

wfb_keygen: src/keygen.o
	$(CC) -o $@ $^ $(_LDFLAGS)

wfb_tx_cmd: src/tx_cmd.o
	$(CC) -o $@ $^ $(LDFLAGS)

wfb_tun: src/wfb_tun.o
	$(CC) -o $@ $^ $(LDFLAGS) -levent_core

wfb_token_scheduler: src/main_token_scheduler.o src/token_scheduler.o src/token_authorization_ipc.o src/wifibroadcast.o
	$(CXX) -o $@ $^ $(LDFLAGS)

wfb_v6_uplink: src/v6_uplink.o src/v6_plaintext_fec_tx.o src/tx.v6_test.o src/rx.rx_test.o src/radiotap.rx_test.o src/zfex.rx_test.o src/wifibroadcast.rx_test.o src/control_envelope.rx_test.o src/token_scheduler.o src/token_authorization.o src/token_authorization_ipc.o src/token_event_ipc.o src/tx_token_gate.o
	$(CXX) -o $@ $^ $(_LDFLAGS) -lpcap

kcp_tools: kcp_small_sender kcp_small_receiver

kcp_small_sender: src/kcp_small_sender.o src/ikcp.o
	$(CXX) -o $@ $^ $(LDFLAGS) -lcrypto

kcp_small_receiver: src/kcp_small_receiver.o src/ikcp.o
	$(CXX) -o $@ $^ $(LDFLAGS) -lcrypto

wfb_rtsp: src/rtsp_server.c
	$(CC) $(_CFLAGS) $(shell pkg-config --cflags gstreamer-rtsp-server-1.0) -o $@ $^ $(LDFLAGS) $(shell pkg-config --libs gstreamer-rtsp-server-1.0)

baseline_split_test:
	PYTHONPATH=`pwd` $(PYTHON) -m twisted.trial wfb_ng.tests.test_txrx.TXRXTestCase wfb_ng.tests.test_txrx.KeyDerivationTestCase
	PYTHONPATH=`pwd` $(PYTHON) -m twisted.trial wfb_ng.tests.test_tuntap.TUNTAPTestCase

test: test_v6

test_v6: build_v6 $(V6_DEFAULT_TESTS)
	./fec_test
	./libsodium_test
	./control_envelope_test
	./v6_uplink_queue_test
	./v6_uplink_downlink_nonce_test

acceptance_v6_realhw:
	@echo "v6 正式三机入口已切换到手动手册：tests/real_hardware/v6新底座无SSH手动上行演示手册.md"
	@echo "请按手册执行 tests/real_hardware/v6_manual_uplink_demo.sh。"

rpm:  all_bin wfb_rtsp $(ENV)
	rm -rf dist
	$$(PATH=$(ENV)/bin:$(ENV)/local/bin:$(PATH) which python3) ./setup.py bdist_rpm --force-arch $(ARCH) --requires python3-twisted,python3-pyroute2,python3-pyserial,python3-msgpack,python3-jinja2,python3-yaml,socat,iw
	rm -rf wfb_ng.egg-info/

deb:  all_bin wfb_rtsp $(ENV)
	rm -rf deb_dist
	$$(PATH=$(ENV)/bin:$(ENV)/local/bin:$(PATH) which python3) ./setup.py --command-packages=stdeb.command sdist_dsc --debian-version 0~$(OS_CODENAME) bdist_deb
	rm -rf wfb_ng.egg-info/ wfb-ng-$(VERSION).tar.gz

bdist: all_bin wfb_rtsp
	rm -rf dist
	$$(PATH=$(ENV)/bin:$(ENV)/local/bin:$(PATH) which python3) ./setup.py bdist --plat-name linux-$(ARCH)
	rm -rf wfb_ng.egg-info/

check:
	cppcheck --force --std=c++11 --library=std --library=posix --library=gnu --inline-suppr --template=gcc --enable=all --suppress=cstyleCast --suppress=missingOverride --suppress=missingIncludeSystem src/
	make clean
	make CFLAGS="$(CFLAGS) -g -fno-omit-frame-pointer -fsanitize=address -fsanitize=undefined -fsanitize=pointer-compare -fsanitize=pointer-subtract -fsanitize=leak -fsanitize-address-use-after-scope" LDFLAGS="-static-libasan -fsanitize=address -fsanitize=undefined -fsanitize=pointer-compare -fsanitize=pointer-subtract -fsanitize=leak -fsanitize-address-use-after-scope" test
	make clean

pylint:
	pylint --disable=R,C wfb_ng/*.py

clean:
	rm -rf env wfb_rx wfb_tx wfb_tx_cmd wfb_tun wfb_token_scheduler wfb_token_namespace_bridge wfb_v6_uplink v6_uplink_downlink_nonce_test kcp_small_sender kcp_small_receiver wfb_rtsp wfb_keygen dist deb_dist build wfb_ng.egg-info wfb_ng-*.tar.gz _trial_temp *~ src/*.o fec_test libsodium_test token_scheduler_test control_envelope_test rx_token_listener_test tx_data_source_gate_test token_authorization_test tx_token_gate_test token_authorization_ipc_test token_namespace_bridge_test tx_authorization_integration_test v6_uplink_queue_test src/*.rx_test.o src/*.rx_gate_test.o src/*.v6_test.o

deb_docker:  /opt/qemu/bin
	@if ! [ -d /opt/qemu ]; then echo "Docker cross build requires patched QEMU!\nApply ./scripts/qemu/qemu.patch to qemu-7.2.0 and build it:\n  ./configure --prefix=/opt/qemu --static --disable-system && make && sudo make install"; exit 1; fi
	if ! ls /proc/sys/fs/binfmt_misc | grep -q qemu ; then sudo ./scripts/qemu/qemu-binfmt-conf.sh --qemu-path /opt/qemu/bin --persistent yes; fi
	cp -a Makefile docker/src/
	TAG="wfb-ng:build-`date +%s`"; docker build --platform linux/$(DOCKER_ARCH) -t $$TAG --build-arg SRC_IMAGE=$(DOCKER_SRC_IMAGE) --build-arg QEMU_CPU=$(QEMU_CPU) -f docker/Dockerfile.debian docker && \
	docker run --privileged --platform linux/$(DOCKER_ARCH) -i --rm -v $(PWD):/build $$TAG bash -c "trap 'chown -R --reference=/build/. /build' EXIT; export VERSION=$(VERSION) COMMIT=$(COMMIT) SOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH) CFLAGS='$(CFLAGS)' && /sbin/sysctl net.unix.max_dgram_qlen=512 && cd /build && make clean && make test && make deb"
	docker image ls -q "wfb-ng:build-*" | uniq | tail -n+6 | while read i ; do docker rmi -f $$i; done

rpm_docker:  /opt/qemu/bin
	@if ! [ -d /opt/qemu ]; then echo "Docker cross build requires patched QEMU!\nApply ./scripts/qemu/qemu.patch to qemu-7.2.0 and build it:\n  ./configure --prefix=/opt/qemu --static --disable-system && make && sudo make install"; exit 1; fi
	if ! ls /proc/sys/fs/binfmt_misc | grep -q qemu ; then sudo ./scripts/qemu/qemu-binfmt-conf.sh --qemu-path /opt/qemu/bin --persistent yes; fi
	cp -a Makefile docker/src/
	TAG="wfb-ng:build-`date +%s`"; docker build --platform linux/$(DOCKER_ARCH) -t $$TAG --build-arg SRC_IMAGE=$(DOCKER_SRC_IMAGE) --build-arg QEMU_CPU=$(QEMU_CPU) -f docker/Dockerfile.redhat docker && \
	docker run --privileged --platform linux/$(DOCKER_ARCH) -i --rm -v $(PWD):/build $$TAG bash -c "trap 'chown -R --reference=/build/. /build' EXIT; export VERSION=$(VERSION) COMMIT=$(COMMIT) SOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH) CFLAGS='$(CFLAGS)' && /sbin/sysctl net.unix.max_dgram_qlen=512 && cd /build && make clean && make test && make rpm"
	docker image ls -q "wfb-ng:build-*" | uniq | tail -n+6 | while read i ; do docker rmi -f $$i; done
