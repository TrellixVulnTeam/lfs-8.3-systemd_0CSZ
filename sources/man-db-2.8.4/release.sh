#! /bin/sh
set -e

# Build automatically generated files
./bootstrap

# Basic configure to get 'make distcheck'
./configure

# Make sure parsers are fresh
if [ src/lexgrog.l -nt src/lexgrog.c ]; then
	rm -f src/lexgrog.c
fi
if [ src/zsoelim.l -nt src/zsoelim.c ]; then
	rm -f src/zsoelim.c
fi

make distcheck
