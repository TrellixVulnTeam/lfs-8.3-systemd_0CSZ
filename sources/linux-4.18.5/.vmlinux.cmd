cmd_vmlinux := /bin/sh scripts/link-vmlinux.sh ld -m elf_x86_64  -z max-page-size=0x200000 --emit-relocs  --build-id ;  true
