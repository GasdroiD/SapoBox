CC ?= cc
CFLAGS ?= -O3 -DNDEBUG
NATIVE = sapobox/converter/libconverter.so
LZ4 = sapobox/converter/ampr_pack_cli
V = vendor/converter

.PHONY: all test deb clean
all: $(NATIVE) $(LZ4)
$(NATIVE): native/bridge.c $(wildcard $(V)/ps5gc/*)
	$(CC) $(CFLAGS) -std=gnu11 -D_FILE_OFFSET_BITS=64 -fPIC -shared -pthread \
	 -ffunction-sections -fdata-sections -Wl,--gc-sections -Wl,-z,defs \
	 native/bridge.c $(V)/ps5gc/ampr_index.c $(V)/ps5gc/pfs_validate_hash.c \
	 -lz -o $@
$(LZ4): native/lz4_runtime_stub.c $(wildcard $(V)/lz4gc/* $(V)/lz4/*)
	$(CC) $(CFLAGS) -std=gnu11 -D_FILE_OFFSET_BITS=64 -Wall -Wextra \
	 -I$(V)/lz4gc -I$(V)/lz4 -pthread \
	 $(V)/lz4gc/ampr_pack_cli.c $(V)/lz4gc/ampr_pack.c \
	 $(V)/lz4gc/ampr_pack_profile.c $(V)/lz4gc/ampr_pack_toml.c \
	 $(V)/lz4gc/ampr_index.c native/lz4_runtime_stub.c \
	 $(V)/lz4/lz4.c $(V)/lz4/lz4hc.c -lz -o $@
test: all
	python3 -m unittest discover -s tests -t . -v
deb: all
	python3 packaging/build_deb.py
clean:
	rm -f $(NATIVE) $(LZ4)
