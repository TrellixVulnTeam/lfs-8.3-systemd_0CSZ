cmd_scripts/unifdef := gcc -Wp,-MD,scripts/.unifdef.d -Wall -Wmissing-prototypes -Wstrict-prototypes -O2 -fomit-frame-pointer -std=gnu89    -I./tools/include   -o scripts/unifdef scripts/unifdef.c  

source_scripts/unifdef := scripts/unifdef.c

deps_scripts/unifdef := \
  /tools/include/stdc-predef.h \
  /tools/include/sys/types.h \
  /tools/include/features.h \
  /tools/include/sys/cdefs.h \
  /tools/include/bits/wordsize.h \
  /tools/include/bits/long-double.h \
  /tools/include/gnu/stubs.h \
  /tools/include/gnu/stubs-64.h \
  /tools/include/bits/types.h \
  /tools/include/bits/typesizes.h \
  /tools/include/bits/types/clock_t.h \
  /tools/include/bits/types/clockid_t.h \
  /tools/include/bits/types/time_t.h \
  /tools/include/bits/types/timer_t.h \
  /tools/lib/gcc/x86_64-pc-linux-gnu/8.2.0/include/stddef.h \
  /tools/include/bits/stdint-intn.h \
  /tools/include/endian.h \
  /tools/include/bits/endian.h \
  /tools/include/bits/byteswap.h \
  /tools/include/bits/uintn-identity.h \
  /tools/include/sys/select.h \
  /tools/include/bits/select.h \
  /tools/include/bits/types/sigset_t.h \
  /tools/include/bits/types/__sigset_t.h \
  /tools/include/bits/types/struct_timeval.h \
  /tools/include/bits/types/struct_timespec.h \
  /tools/include/bits/pthreadtypes.h \
  /tools/include/bits/thread-shared-types.h \
  /tools/include/bits/pthreadtypes-arch.h \
  /tools/include/sys/stat.h \
  /tools/include/bits/stat.h \
  /tools/include/ctype.h \
  /tools/include/bits/types/locale_t.h \
  /tools/include/bits/types/__locale_t.h \
  /tools/include/err.h \
  /tools/lib/gcc/x86_64-pc-linux-gnu/8.2.0/include/stdarg.h \
  /tools/include/errno.h \
  /tools/include/bits/errno.h \
  /tools/include/linux/errno.h \
  /tools/include/asm/errno.h \
  /tools/include/asm-generic/errno.h \
  /tools/include/asm-generic/errno-base.h \
  /tools/lib/gcc/x86_64-pc-linux-gnu/8.2.0/include/stdbool.h \
  /tools/include/stdio.h \
  /tools/include/bits/libc-header-start.h \
  /tools/include/bits/types/__fpos_t.h \
  /tools/include/bits/types/__mbstate_t.h \
  /tools/include/bits/types/__fpos64_t.h \
  /tools/include/bits/types/__FILE.h \
  /tools/include/bits/types/FILE.h \
  /tools/include/bits/types/struct_FILE.h \
  /tools/include/bits/stdio_lim.h \
  /tools/include/bits/sys_errlist.h \
  /tools/include/bits/stdio.h \
  /tools/include/stdlib.h \
  /tools/include/bits/waitflags.h \
  /tools/include/bits/waitstatus.h \
  /tools/include/bits/floatn.h \
  /tools/include/bits/floatn-common.h \
  /tools/include/alloca.h \
  /tools/include/bits/stdlib-bsearch.h \
  /tools/include/bits/stdlib-float.h \
  /tools/include/string.h \
  /tools/include/strings.h \
  /tools/include/unistd.h \
  /tools/include/bits/posix_opt.h \
  /tools/include/bits/environments.h \
  /tools/include/bits/confname.h \
  /tools/include/bits/getopt_posix.h \
  /tools/include/bits/getopt_core.h \

scripts/unifdef: $(deps_scripts/unifdef)

$(deps_scripts/unifdef):
