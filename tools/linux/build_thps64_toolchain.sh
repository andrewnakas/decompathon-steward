#!/bin/sh
# Linux twin of thps64's toolchain/build.sh: the same sources and revisions, built natively for a 32-bit Linux
# host with zig (x86-linux-musl) instead of 32-bit MinGW; no Windows patches, no compat shims.
#   sh build_thps64_toolchain.sh THPS64_CHECKOUT [WORKDIR]   (default ~/thps64-tools)
#   -> WORKDIR/bin/mips-as.exe (patched KMC gas 2.6), WORKDIR/gcc-2.8.1-pm/cc1.exe (the project compiler)
# The .exe names are kept because tools/match.py appends .exe to the gas name.
set -e
SRC=$(cd "$1" && pwd)/toolchain
W=${2:-$HOME/thps64-tools}
ZIG=${ZIG:-$HOME/.local/zig/zig}
GCC281_REV=$(sed -n 's/^GCC281_REV=//p' "$SRC/build.sh")
BU_REV=$(sed -n 's/^BU_REV=//p' "$SRC/build.sh")
mkdir -p "$W/bin"
cat > "$W/bin/cc32" <<CC
#!/bin/sh
exec "$ZIG" cc -target x86-linux-musl -std=gnu89 -fwrapv -fno-strict-aliasing -w -Wno-error -Wno-implicit-function-declaration -Wno-int-conversion -Wno-return-type -Wno-incompatible-pointer-types -fno-sanitize=undefined "\$@"
CC
chmod +x "$W/bin/cc32"
CC="$W/bin/cc32"
export ZIG_GLOBAL_CACHE_DIR="$W/zig-cache" ZIG_LOCAL_CACHE_DIR="$W/zig-cache"
AR="$ZIG ar"

cd "$W"
[ -d mips-binutils-2.6 ] || git clone -q https://github.com/decompals/mips-binutils-2.6
cd mips-binutils-2.6 && git checkout -q -f $BU_REV && git clean -qfdx
CC="$CC" ./configure --target=mips-kmc-elf --prefix=/opt/cross --disable-gprof --disable-gdb --disable-werror \
  --host=i386-pc-linux --build=i386-pc-linux > ../bu-configure.log
python3 "$SRC/gas_patch.py" gas/targ-cpu.c gas/as.h
for m in THPS_AS_NO_LOAD_SWAP THPS_AS_NO_STORE_SWAP THPS_AS_COP_INTERLOCKS THPS_AS_NO_MUL_SWAP; do grep -q $m gas/targ-cpu.c; done
for d in bfd opcodes; do make -C $d CC="$CC" CFLAGS=-O1 AR="$AR" RANLIB=true >> ../bu-make.log 2>&1; done
# libiberty's link probe cannot run under zig (dummy.c calls every function with no arguments); musl lacks only
# sys_siglist
(cd libiberty && touch dummy.o && echo "undefined reference to \`sys_siglist'" > errors &&
  printf '#ifndef NEED_sys_siglist\n#define NEED_sys_siglist\n#endif\n' > lconfig.h)
make -C libiberty CC="$CC" CFLAGS=-O1 AR="$AR" RANLIB=true >> ../bu-make.log 2>&1
make -C gas CC="$CC" CFLAGS=-O1 AR="$AR" RANLIB=true >> ../bu-make.log 2>&1
cp gas/as.new "$W/bin/mips-as.exe"

cd "$W"
[ -d gcc-2.8.1-pm ] || git -c core.autocrlf=false clone -q https://github.com/pmret/gcc-papermario gcc-2.8.1-pm
cd gcc-2.8.1-pm && git checkout -q -f $GCC281_REV && git clean -qfdx
CC="$CC" ./configure --target=mips-nintendo-nu64 --prefix=/opt/cross --with-gnu-as --disable-gprof --disable-gdb \
  --disable-werror --host=i386-pc-linux --build=i386-pc-linux > ../gcc281-configure.log
touch c-parse.c c-gperf.h   # generated files newer than their sources: no bison/gperf needed
make CC="$CC" HOST_CC="$CC" CFLAGS=-O1 cc1 > ../gcc281-make.log 2>&1
cp cc1 cc1.exe
# C++ parts of the game (tools/match.py `/* compiler: cc1plus */`): g++ from the same sources
touch cp/parse.c cp/parse.h cp/hash.h
make CC="$CC" HOST_CC="$CC" CFLAGS=-O1 cc1plus >> ../gcc281-make.log 2>&1
cp cc1plus cc1plus.exe
echo "toolchain ready: $W/gcc-2.8.1-pm/cc1.exe, $W/bin/mips-as.exe"
