#!/usr/bin/env python3
# Copyright 2016-2017 The Meson development team

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import stat
import shlex
import subprocess
import re, json
import tempfile
import textwrap
import os
import shutil
import unittest
from unittest import mock
from configparser import ConfigParser
from glob import glob
from pathlib import (PurePath, Path)

import mesonbuild.mlog
import mesonbuild.compilers
import mesonbuild.environment
import mesonbuild.mesonlib
import mesonbuild.coredata
import mesonbuild.modules.gnome
from mesonbuild.interpreter import ObjectHolder
from mesonbuild.mesonlib import (
    is_windows, is_osx, is_cygwin, is_dragonflybsd, is_openbsd,
    windows_proof_rmtree, python_command, version_compare,
    grab_leading_numbers, BuildDirLock
)
from mesonbuild.environment import Environment, detect_ninja
from mesonbuild.mesonlib import MesonException, EnvironmentException
from mesonbuild.dependencies import PkgConfigDependency, ExternalProgram
import mesonbuild.modules.pkgconfig

from run_tests import exe_suffix, get_fake_options, get_meson_script
from run_tests import get_builddir_target_args, get_backend_commands, Backend
from run_tests import ensure_backend_detects_changes, run_configure_inprocess
from run_tests import should_run_linux_cross_tests


def get_dynamic_section_entry(fname, entry):
    if is_cygwin() or is_osx():
            raise unittest.SkipTest('Test only applicable to ELF platforms')

    try:
        raw_out = subprocess.check_output(['readelf', '-d', fname],
                                          universal_newlines=True)
    except FileNotFoundError:
        # FIXME: Try using depfixer.py:Elf() as a fallback
        raise unittest.SkipTest('readelf not found')
    pattern = re.compile(entry + r': \[(.*?)\]')
    for line in raw_out.split('\n'):
        m = pattern.search(line)
        if m is not None:
            return m.group(1)
    return None # The file did not contain the specified entry.

def get_soname(fname):
    return get_dynamic_section_entry(fname, 'soname')


def get_rpath(fname):
    return get_dynamic_section_entry(fname, r'(?:rpath|runpath)')

def is_ci():
    if 'TRAVIS' in os.environ or 'APPVEYOR' in os.environ:
        return True
    return False

def skipIfNoPkgconfig(f):
    '''
    Skip this test if no pkg-config is found, unless we're on Travis or
    Appveyor CI.  This allows users to run our test suite without having
    pkg-config installed on, f.ex., macOS, while ensuring that our CI does not
    silently skip the test because of misconfiguration.

    Note: Yes, we provide pkg-config even while running Windows CI
    '''
    def wrapped(*args, **kwargs):
        if not is_ci() and shutil.which('pkg-config') is None:
            raise unittest.SkipTest('pkg-config not found')
        return f(*args, **kwargs)
    return wrapped

class PatchModule:
    '''
    Fancy monkey-patching! Whee! Can't use mock.patch because it only
    patches in the local namespace.
    '''
    def __init__(self, func, name, impl):
        self.func = func
        assert(isinstance(name, str))
        self.func_name = name
        self.old_impl = None
        self.new_impl = impl

    def __enter__(self):
        self.old_impl = self.func
        exec('{} = self.new_impl'.format(self.func_name))

    def __exit__(self, *args):
        exec('{} = self.old_impl'.format(self.func_name))


class InternalTests(unittest.TestCase):

    def test_version_number(self):
        searchfunc = mesonbuild.environment.search_version
        self.assertEqual(searchfunc('foobar 1.2.3'), '1.2.3')
        self.assertEqual(searchfunc('1.2.3'), '1.2.3')
        self.assertEqual(searchfunc('foobar 2016.10.28 1.2.3'), '1.2.3')
        self.assertEqual(searchfunc('2016.10.28 1.2.3'), '1.2.3')
        self.assertEqual(searchfunc('foobar 2016.10.128'), 'unknown version')
        self.assertEqual(searchfunc('2016.10.128'), 'unknown version')

    def test_mode_symbolic_to_bits(self):
        modefunc = mesonbuild.mesonlib.FileMode.perms_s_to_bits
        self.assertEqual(modefunc('---------'), 0)
        self.assertEqual(modefunc('r--------'), stat.S_IRUSR)
        self.assertEqual(modefunc('---r-----'), stat.S_IRGRP)
        self.assertEqual(modefunc('------r--'), stat.S_IROTH)
        self.assertEqual(modefunc('-w-------'), stat.S_IWUSR)
        self.assertEqual(modefunc('----w----'), stat.S_IWGRP)
        self.assertEqual(modefunc('-------w-'), stat.S_IWOTH)
        self.assertEqual(modefunc('--x------'), stat.S_IXUSR)
        self.assertEqual(modefunc('-----x---'), stat.S_IXGRP)
        self.assertEqual(modefunc('--------x'), stat.S_IXOTH)
        self.assertEqual(modefunc('--S------'), stat.S_ISUID)
        self.assertEqual(modefunc('-----S---'), stat.S_ISGID)
        self.assertEqual(modefunc('--------T'), stat.S_ISVTX)
        self.assertEqual(modefunc('--s------'), stat.S_ISUID | stat.S_IXUSR)
        self.assertEqual(modefunc('-----s---'), stat.S_ISGID | stat.S_IXGRP)
        self.assertEqual(modefunc('--------t'), stat.S_ISVTX | stat.S_IXOTH)
        self.assertEqual(modefunc('rwx------'), stat.S_IRWXU)
        self.assertEqual(modefunc('---rwx---'), stat.S_IRWXG)
        self.assertEqual(modefunc('------rwx'), stat.S_IRWXO)
        # We could keep listing combinations exhaustively but that seems
        # tedious and pointless. Just test a few more.
        self.assertEqual(modefunc('rwxr-xr-x'),
                         stat.S_IRWXU |
                         stat.S_IRGRP | stat.S_IXGRP |
                         stat.S_IROTH | stat.S_IXOTH)
        self.assertEqual(modefunc('rw-r--r--'),
                         stat.S_IRUSR | stat.S_IWUSR |
                         stat.S_IRGRP |
                         stat.S_IROTH)
        self.assertEqual(modefunc('rwsr-x---'),
                         stat.S_IRWXU | stat.S_ISUID |
                         stat.S_IRGRP | stat.S_IXGRP)

    def test_compiler_args_class(self):
        cargsfunc = mesonbuild.compilers.CompilerArgs
        c = mesonbuild.compilers.CCompiler([], 'fake', False)
        # Test that bad initialization fails
        self.assertRaises(TypeError, cargsfunc, [])
        self.assertRaises(TypeError, cargsfunc, [], [])
        self.assertRaises(TypeError, cargsfunc, c, [], [])
        # Test that empty initialization works
        a = cargsfunc(c)
        self.assertEqual(a, [])
        # Test that list initialization works
        a = cargsfunc(['-I.', '-I..'], c)
        self.assertEqual(a, ['-I.', '-I..'])
        # Test that there is no de-dup on initialization
        self.assertEqual(cargsfunc(['-I.', '-I.'], c), ['-I.', '-I.'])

        ## Test that appending works
        a.append('-I..')
        self.assertEqual(a, ['-I..', '-I.'])
        a.append('-O3')
        self.assertEqual(a, ['-I..', '-I.', '-O3'])

        ## Test that in-place addition works
        a += ['-O2', '-O2']
        self.assertEqual(a, ['-I..', '-I.', '-O3', '-O2', '-O2'])
        # Test that removal works
        a.remove('-O2')
        self.assertEqual(a, ['-I..', '-I.', '-O3', '-O2'])
        # Test that de-dup happens on addition
        a += ['-Ifoo', '-Ifoo']
        self.assertEqual(a, ['-Ifoo', '-I..', '-I.', '-O3', '-O2'])

        # .extend() is just +=, so we don't test it

        ## Test that addition works
        # Test that adding a list with just one old arg works and yields the same array
        a = a + ['-Ifoo']
        self.assertEqual(a, ['-Ifoo', '-I..', '-I.', '-O3', '-O2'])
        # Test that adding a list with one arg new and one old works
        a = a + ['-Ifoo', '-Ibaz']
        self.assertEqual(a, ['-Ifoo', '-Ibaz', '-I..', '-I.', '-O3', '-O2'])
        # Test that adding args that must be prepended and appended works
        a = a + ['-Ibar', '-Wall']
        self.assertEqual(a, ['-Ibar', '-Ifoo', '-Ibaz', '-I..', '-I.', '-O3', '-O2', '-Wall'])

        ## Test that reflected addition works
        # Test that adding to a list with just one old arg works and yields the same array
        a = ['-Ifoo'] + a
        self.assertEqual(a, ['-Ibar', '-Ifoo', '-Ibaz', '-I..', '-I.', '-O3', '-O2', '-Wall'])
        # Test that adding to a list with just one new arg that is not pre-pended works
        a = ['-Werror'] + a
        self.assertEqual(a, ['-Ibar', '-Ifoo', '-Ibaz', '-I..', '-I.', '-Werror', '-O3', '-O2', '-Wall'])
        # Test that adding to a list with two new args preserves the order
        a = ['-Ldir', '-Lbah'] + a
        self.assertEqual(a, ['-Ibar', '-Ifoo', '-Ibaz', '-I..', '-I.', '-Ldir', '-Lbah', '-Werror', '-O3', '-O2', '-Wall'])
        # Test that adding to a list with old args does nothing
        a = ['-Ibar', '-Ibaz', '-Ifoo'] + a
        self.assertEqual(a, ['-Ibar', '-Ifoo', '-Ibaz', '-I..', '-I.', '-Ldir', '-Lbah', '-Werror', '-O3', '-O2', '-Wall'])

        ## Test that adding libraries works
        l = cargsfunc(c, ['-Lfoodir', '-lfoo'])
        self.assertEqual(l, ['-Lfoodir', '-lfoo'])
        # Adding a library and a libpath appends both correctly
        l += ['-Lbardir', '-lbar']
        self.assertEqual(l, ['-Lbardir', '-Lfoodir', '-lfoo', '-lbar'])
        # Adding the same library again does nothing
        l += ['-lbar']
        self.assertEqual(l, ['-Lbardir', '-Lfoodir', '-lfoo', '-lbar'])

        ## Test that 'direct' append and extend works
        l = cargsfunc(c, ['-Lfoodir', '-lfoo'])
        self.assertEqual(l, ['-Lfoodir', '-lfoo'])
        # Direct-adding a library and a libpath appends both correctly
        l.extend_direct(['-Lbardir', '-lbar'])
        self.assertEqual(l, ['-Lfoodir', '-lfoo', '-Lbardir', '-lbar'])
        # Direct-adding the same library again still adds it
        l.append_direct('-lbar')
        self.assertEqual(l, ['-Lfoodir', '-lfoo', '-Lbardir', '-lbar', '-lbar'])
        # Direct-adding with absolute path deduplicates
        l.append_direct('/libbaz.a')
        self.assertEqual(l, ['-Lfoodir', '-lfoo', '-Lbardir', '-lbar', '-lbar', '/libbaz.a'])
        # Adding libbaz again does nothing
        l.append_direct('/libbaz.a')
        self.assertEqual(l, ['-Lfoodir', '-lfoo', '-Lbardir', '-lbar', '-lbar', '/libbaz.a'])

    def test_string_templates_substitution(self):
        dictfunc = mesonbuild.mesonlib.get_filenames_templates_dict
        substfunc = mesonbuild.mesonlib.substitute_values
        ME = mesonbuild.mesonlib.MesonException

        # Identity
        self.assertEqual(dictfunc([], []), {})

        # One input, no outputs
        inputs = ['bar/foo.c.in']
        outputs = []
        ret = dictfunc(inputs, outputs)
        d = {'@INPUT@': inputs, '@INPUT0@': inputs[0],
             '@PLAINNAME@': 'foo.c.in', '@BASENAME@': 'foo.c'}
        # Check dictionary
        self.assertEqual(ret, d)
        # Check substitutions
        cmd = ['some', 'ordinary', 'strings']
        self.assertEqual(substfunc(cmd, d), cmd)
        cmd = ['@INPUT@.out', 'ordinary', 'strings']
        self.assertEqual(substfunc(cmd, d), [inputs[0] + '.out'] + cmd[1:])
        cmd = ['@INPUT0@.out', '@PLAINNAME@.ok', 'strings']
        self.assertEqual(substfunc(cmd, d),
                         [inputs[0] + '.out'] + [d['@PLAINNAME@'] + '.ok'] + cmd[2:])
        cmd = ['@INPUT@', '@BASENAME@.hah', 'strings']
        self.assertEqual(substfunc(cmd, d),
                         inputs + [d['@BASENAME@'] + '.hah'] + cmd[2:])
        cmd = ['@OUTPUT@']
        self.assertRaises(ME, substfunc, cmd, d)

        # One input, one output
        inputs = ['bar/foo.c.in']
        outputs = ['out.c']
        ret = dictfunc(inputs, outputs)
        d = {'@INPUT@': inputs, '@INPUT0@': inputs[0],
             '@PLAINNAME@': 'foo.c.in', '@BASENAME@': 'foo.c',
             '@OUTPUT@': outputs, '@OUTPUT0@': outputs[0], '@OUTDIR@': '.'}
        # Check dictionary
        self.assertEqual(ret, d)
        # Check substitutions
        cmd = ['some', 'ordinary', 'strings']
        self.assertEqual(substfunc(cmd, d), cmd)
        cmd = ['@INPUT@.out', '@OUTPUT@', 'strings']
        self.assertEqual(substfunc(cmd, d),
                         [inputs[0] + '.out'] + outputs + cmd[2:])
        cmd = ['@INPUT0@.out', '@PLAINNAME@.ok', '@OUTPUT0@']
        self.assertEqual(substfunc(cmd, d),
                         [inputs[0] + '.out', d['@PLAINNAME@'] + '.ok'] + outputs)
        cmd = ['@INPUT@', '@BASENAME@.hah', 'strings']
        self.assertEqual(substfunc(cmd, d),
                         inputs + [d['@BASENAME@'] + '.hah'] + cmd[2:])

        # One input, one output with a subdir
        outputs = ['dir/out.c']
        ret = dictfunc(inputs, outputs)
        d = {'@INPUT@': inputs, '@INPUT0@': inputs[0],
             '@PLAINNAME@': 'foo.c.in', '@BASENAME@': 'foo.c',
             '@OUTPUT@': outputs, '@OUTPUT0@': outputs[0], '@OUTDIR@': 'dir'}
        # Check dictionary
        self.assertEqual(ret, d)

        # Two inputs, no outputs
        inputs = ['bar/foo.c.in', 'baz/foo.c.in']
        outputs = []
        ret = dictfunc(inputs, outputs)
        d = {'@INPUT@': inputs, '@INPUT0@': inputs[0], '@INPUT1@': inputs[1]}
        # Check dictionary
        self.assertEqual(ret, d)
        # Check substitutions
        cmd = ['some', 'ordinary', 'strings']
        self.assertEqual(substfunc(cmd, d), cmd)
        cmd = ['@INPUT@', 'ordinary', 'strings']
        self.assertEqual(substfunc(cmd, d), inputs + cmd[1:])
        cmd = ['@INPUT0@.out', 'ordinary', 'strings']
        self.assertEqual(substfunc(cmd, d), [inputs[0] + '.out'] + cmd[1:])
        cmd = ['@INPUT0@.out', '@INPUT1@.ok', 'strings']
        self.assertEqual(substfunc(cmd, d), [inputs[0] + '.out', inputs[1] + '.ok'] + cmd[2:])
        cmd = ['@INPUT0@', '@INPUT1@', 'strings']
        self.assertEqual(substfunc(cmd, d), inputs + cmd[2:])
        # Many inputs, can't use @INPUT@ like this
        cmd = ['@INPUT@.out', 'ordinary', 'strings']
        self.assertRaises(ME, substfunc, cmd, d)
        # Not enough inputs
        cmd = ['@INPUT2@.out', 'ordinary', 'strings']
        self.assertRaises(ME, substfunc, cmd, d)
        # Too many inputs
        cmd = ['@PLAINNAME@']
        self.assertRaises(ME, substfunc, cmd, d)
        cmd = ['@BASENAME@']
        self.assertRaises(ME, substfunc, cmd, d)
        # No outputs
        cmd = ['@OUTPUT@']
        self.assertRaises(ME, substfunc, cmd, d)
        cmd = ['@OUTPUT0@']
        self.assertRaises(ME, substfunc, cmd, d)
        cmd = ['@OUTDIR@']
        self.assertRaises(ME, substfunc, cmd, d)

        # Two inputs, one output
        outputs = ['dir/out.c']
        ret = dictfunc(inputs, outputs)
        d = {'@INPUT@': inputs, '@INPUT0@': inputs[0], '@INPUT1@': inputs[1],
             '@OUTPUT@': outputs, '@OUTPUT0@': outputs[0], '@OUTDIR@': 'dir'}
        # Check dictionary
        self.assertEqual(ret, d)
        # Check substitutions
        cmd = ['some', 'ordinary', 'strings']
        self.assertEqual(substfunc(cmd, d), cmd)
        cmd = ['@OUTPUT@', 'ordinary', 'strings']
        self.assertEqual(substfunc(cmd, d), outputs + cmd[1:])
        cmd = ['@OUTPUT@.out', 'ordinary', 'strings']
        self.assertEqual(substfunc(cmd, d), [outputs[0] + '.out'] + cmd[1:])
        cmd = ['@OUTPUT0@.out', '@INPUT1@.ok', 'strings']
        self.assertEqual(substfunc(cmd, d), [outputs[0] + '.out', inputs[1] + '.ok'] + cmd[2:])
        # Many inputs, can't use @INPUT@ like this
        cmd = ['@INPUT@.out', 'ordinary', 'strings']
        self.assertRaises(ME, substfunc, cmd, d)
        # Not enough inputs
        cmd = ['@INPUT2@.out', 'ordinary', 'strings']
        self.assertRaises(ME, substfunc, cmd, d)
        # Not enough outputs
        cmd = ['@OUTPUT2@.out', 'ordinary', 'strings']
        self.assertRaises(ME, substfunc, cmd, d)

        # Two inputs, two outputs
        outputs = ['dir/out.c', 'dir/out2.c']
        ret = dictfunc(inputs, outputs)
        d = {'@INPUT@': inputs, '@INPUT0@': inputs[0], '@INPUT1@': inputs[1],
             '@OUTPUT@': outputs, '@OUTPUT0@': outputs[0], '@OUTPUT1@': outputs[1],
             '@OUTDIR@': 'dir'}
        # Check dictionary
        self.assertEqual(ret, d)
        # Check substitutions
        cmd = ['some', 'ordinary', 'strings']
        self.assertEqual(substfunc(cmd, d), cmd)
        cmd = ['@OUTPUT@', 'ordinary', 'strings']
        self.assertEqual(substfunc(cmd, d), outputs + cmd[1:])
        cmd = ['@OUTPUT0@', '@OUTPUT1@', 'strings']
        self.assertEqual(substfunc(cmd, d), outputs + cmd[2:])
        cmd = ['@OUTPUT0@.out', '@INPUT1@.ok', '@OUTDIR@']
        self.assertEqual(substfunc(cmd, d), [outputs[0] + '.out', inputs[1] + '.ok', 'dir'])
        # Many inputs, can't use @INPUT@ like this
        cmd = ['@INPUT@.out', 'ordinary', 'strings']
        self.assertRaises(ME, substfunc, cmd, d)
        # Not enough inputs
        cmd = ['@INPUT2@.out', 'ordinary', 'strings']
        self.assertRaises(ME, substfunc, cmd, d)
        # Not enough outputs
        cmd = ['@OUTPUT2@.out', 'ordinary', 'strings']
        self.assertRaises(ME, substfunc, cmd, d)
        # Many outputs, can't use @OUTPUT@ like this
        cmd = ['@OUTPUT@.out', 'ordinary', 'strings']
        self.assertRaises(ME, substfunc, cmd, d)

    def test_needs_exe_wrapper_override(self):
        config = ConfigParser()
        config['binaries'] = {
            'c': '\'/usr/bin/gcc\'',
        }
        config['host_machine'] = {
            'system': '\'linux\'',
            'cpu_family': '\'arm\'',
            'cpu': '\'armv7\'',
            'endian': '\'little\'',
        }
        # Can not be used as context manager because we need to
        # open it a second time and this is not possible on
        # Windows.
        configfile = tempfile.NamedTemporaryFile(mode='w+', delete=False)
        configfilename = configfile.name
        config.write(configfile)
        configfile.flush()
        configfile.close()
        detected_value = mesonbuild.environment.CrossBuildInfo(configfile.name).need_exe_wrapper()
        os.unlink(configfilename)

        desired_value = not detected_value
        config['properties'] = {
            'needs_exe_wrapper': 'true' if desired_value else 'false'
        }

        configfile = tempfile.NamedTemporaryFile(mode='w+', delete=False)
        configfilename = configfile.name
        config.write(configfile)
        configfile.close()
        forced_value = mesonbuild.environment.CrossBuildInfo(configfile.name).need_exe_wrapper()
        os.unlink(configfilename)

        self.assertEqual(forced_value, desired_value)

    def test_listify(self):
        listify = mesonbuild.mesonlib.listify
        # Test sanity
        self.assertEqual([1], listify(1))
        self.assertEqual([], listify([]))
        self.assertEqual([1], listify([1]))
        # Test flattening
        self.assertEqual([1, 2, 3], listify([1, [2, 3]]))
        self.assertEqual([1, 2, 3], listify([1, [2, [3]]]))
        self.assertEqual([1, [2, [3]]], listify([1, [2, [3]]], flatten=False))
        # Test flattening and unholdering
        holder1 = ObjectHolder(1)
        holder3 = ObjectHolder(3)
        self.assertEqual([holder1], listify(holder1))
        self.assertEqual([holder1], listify([holder1]))
        self.assertEqual([holder1, 2], listify([holder1, 2]))
        self.assertEqual([holder1, 2, 3], listify([holder1, 2, [3]]))
        self.assertEqual([1], listify(holder1, unholder=True))
        self.assertEqual([1], listify([holder1], unholder=True))
        self.assertEqual([1, 2], listify([holder1, 2], unholder=True))
        self.assertEqual([1, 2, 3], listify([holder1, 2, [holder3]], unholder=True))
        # Unholding doesn't work recursively when not flattening
        self.assertEqual([1, [2], [holder3]], listify([holder1, [2], [holder3]], unholder=True, flatten=False))

    def test_extract_as_list(self):
        extract = mesonbuild.mesonlib.extract_as_list
        # Test sanity
        kwargs = {'sources': [1, 2, 3]}
        self.assertEqual([1, 2, 3], extract(kwargs, 'sources'))
        self.assertEqual(kwargs, {'sources': [1, 2, 3]})
        self.assertEqual([1, 2, 3], extract(kwargs, 'sources', pop=True))
        self.assertEqual(kwargs, {})
        # Test unholding
        holder3 = ObjectHolder(3)
        kwargs = {'sources': [1, 2, holder3]}
        self.assertEqual([1, 2, 3], extract(kwargs, 'sources', unholder=True))
        self.assertEqual(kwargs, {'sources': [1, 2, holder3]})
        self.assertEqual([1, 2, 3], extract(kwargs, 'sources', unholder=True, pop=True))
        self.assertEqual(kwargs, {})
        # Test listification
        kwargs = {'sources': [1, 2, 3], 'pch_sources': [4, 5, 6]}
        self.assertEqual([[1, 2, 3], [4, 5, 6]], extract(kwargs, 'sources', 'pch_sources'))

    @unittest.skipIf(not os.path.isdir('docs'), 'Doc dir not found, presumably because this is a tarball release.')
    def test_snippets(self):
        hashcounter = re.compile('^ *(#)+')
        snippet_dir = Path('docs/markdown/snippets')
        self.assertTrue(snippet_dir.is_dir())
        for f in snippet_dir.glob('*'):
            self.assertTrue(f.is_file())
            if f.suffix == '.md':
                with f.open() as snippet:
                    for line in snippet:
                        m = re.match(hashcounter, line)
                        if m:
                            self.assertEqual(len(m.group(0)), 2, 'All headings in snippets must have two hash symbols: ' + f.name)
            else:
                if f.name != 'add_release_note_snippets_here':
                    self.assertTrue(False, 'A file without .md suffix in snippets dir: ' + f.name)

    def test_pkgconfig_module(self):

        class Mock:
            pass

        mock = Mock()
        mock.pcdep = Mock()
        mock.pcdep.name = "some_name"
        mock.version_reqs = []

        # pkgconfig dependency as lib
        deps = mesonbuild.modules.pkgconfig.DependenciesHelper("thislib")
        deps.add_pub_libs([mock])
        self.assertEqual(deps.format_reqs(deps.pub_reqs), "some_name")

        # pkgconfig dependency as requires
        deps = mesonbuild.modules.pkgconfig.DependenciesHelper("thislib")
        deps.add_pub_reqs([mock])
        self.assertEqual(deps.format_reqs(deps.pub_reqs), "some_name")

    def _test_all_naming(self, cc, env, patterns, platform):
        shr = patterns[platform]['shared']
        stc = patterns[platform]['static']
        p = cc.get_library_naming(env, 'shared')
        self.assertEqual(p, shr)
        p = cc.get_library_naming(env, 'static')
        self.assertEqual(p, stc)
        p = cc.get_library_naming(env, 'static-shared')
        self.assertEqual(p, stc + shr)
        p = cc.get_library_naming(env, 'shared-static')
        self.assertEqual(p, shr + stc)
        p = cc.get_library_naming(env, 'default')
        self.assertEqual(p, shr + stc)
        # Test find library by mocking up openbsd
        if platform != 'openbsd':
            return
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, 'libfoo.so.6.0'), 'w') as f:
                f.write('')
            with open(os.path.join(tmpdir, 'libfoo.so.5.0'), 'w') as f:
                f.write('')
            with open(os.path.join(tmpdir, 'libfoo.so.54.0'), 'w') as f:
                f.write('')
            with open(os.path.join(tmpdir, 'libfoo.so.66a.0b'), 'w') as f:
                f.write('')
            with open(os.path.join(tmpdir, 'libfoo.so.70.0.so.1'), 'w') as f:
                f.write('')
            found = cc.find_library_real('foo', env, [tmpdir], '', 'default')
            self.assertEqual(os.path.basename(found[0]), 'libfoo.so.54.0')

    def test_find_library_patterns(self):
        '''
        Unit test for the library search patterns used by find_library()
        '''
        unix_static = ['lib{}.a', '{}.a']
        msvc_static = ['lib{}.a', 'lib{}.lib', '{}.a', '{}.lib']
        # This is the priority list of pattern matching for library searching
        patterns = {'openbsd': {'shared': ['lib{}.so', '{}.so', 'lib{}.so.[0-9]*.[0-9]*'],
                                'static': unix_static},
                    'linux': {'shared': ['lib{}.so', '{}.so'],
                              'static': unix_static},
                    'darwin': {'shared': ['lib{}.dylib', '{}.dylib'],
                               'static': unix_static},
                    'cygwin': {'shared': ['cyg{}.dll', 'cyg{}.dll.a', 'lib{}.dll',
                                          'lib{}.dll.a', '{}.dll', '{}.dll.a'],
                               'static': ['cyg{}.a'] + unix_static},
                    'windows-msvc': {'shared': ['lib{}.lib', '{}.lib'],
                                     'static': msvc_static},
                    'windows-mingw': {'shared': ['lib{}.dll.a', 'lib{}.lib', 'lib{}.dll',
                                                 '{}.dll.a', '{}.lib', '{}.dll'],
                                      'static': msvc_static}}
        env = Environment('', '', get_fake_options(''))
        cc = env.detect_c_compiler(False)
        if is_osx():
            self._test_all_naming(cc, env, patterns, 'darwin')
        elif is_cygwin():
            self._test_all_naming(cc, env, patterns, 'cygwin')
        elif is_windows():
            if cc.get_id() == 'msvc':
                self._test_all_naming(cc, env, patterns, 'windows-msvc')
            else:
                self._test_all_naming(cc, env, patterns, 'windows-mingw')
        else:
            self._test_all_naming(cc, env, patterns, 'linux')
            # Mock OpenBSD since we don't have tests for it
            true = lambda x, y: True
            if not is_openbsd():
                with PatchModule(mesonbuild.compilers.c.for_openbsd,
                                 'mesonbuild.compilers.c.for_openbsd', true):
                    self._test_all_naming(cc, env, patterns, 'openbsd')
            else:
                self._test_all_naming(cc, env, patterns, 'openbsd')
            with PatchModule(mesonbuild.compilers.c.for_darwin,
                             'mesonbuild.compilers.c.for_darwin', true):
                self._test_all_naming(cc, env, patterns, 'darwin')
            with PatchModule(mesonbuild.compilers.c.for_cygwin,
                             'mesonbuild.compilers.c.for_cygwin', true):
                self._test_all_naming(cc, env, patterns, 'cygwin')
            with PatchModule(mesonbuild.compilers.c.for_windows,
                             'mesonbuild.compilers.c.for_windows', true):
                self._test_all_naming(cc, env, patterns, 'windows-mingw')
            cc.id = 'msvc'
            with PatchModule(mesonbuild.compilers.c.for_windows,
                             'mesonbuild.compilers.c.for_windows', true):
                self._test_all_naming(cc, env, patterns, 'windows-msvc')


class BasePlatformTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        src_root = os.path.dirname(__file__)
        src_root = os.path.join(os.getcwd(), src_root)
        self.src_root = src_root
        self.prefix = '/usr'
        self.libdir = 'lib'
        # Get the backend
        # FIXME: Extract this from argv?
        self.backend = getattr(Backend, os.environ.get('MESON_UNIT_TEST_BACKEND', 'ninja'))
        self.meson_args = ['--backend=' + self.backend.name]
        self.meson_cross_file = None
        self.meson_command = python_command + [get_meson_script()]
        self.setup_command = self.meson_command + self.meson_args
        self.mconf_command = self.meson_command + ['configure']
        self.mintro_command = self.meson_command + ['introspect']
        self.wrap_command = self.meson_command + ['wrap']
        # Backend-specific build commands
        self.build_command, self.clean_command, self.test_command, self.install_command, \
            self.uninstall_command = get_backend_commands(self.backend)
        # Test directories
        self.common_test_dir = os.path.join(src_root, 'test cases/common')
        self.vala_test_dir = os.path.join(src_root, 'test cases/vala')
        self.framework_test_dir = os.path.join(src_root, 'test cases/frameworks')
        self.unit_test_dir = os.path.join(src_root, 'test cases/unit')
        # Misc stuff
        self.orig_env = os.environ.copy()
        if self.backend is Backend.ninja:
            self.no_rebuild_stdout = 'ninja: no work to do.'
        else:
            # VS doesn't have a stable output when no changes are done
            # XCode backend is untested with unit tests, help welcome!
            self.no_rebuild_stdout = 'UNKNOWN BACKEND {!r}'.format(self.backend.name)

        self.builddirs = []
        self.new_builddir()

    def change_builddir(self, newdir):
        self.builddir = newdir
        self.privatedir = os.path.join(self.builddir, 'meson-private')
        self.logdir = os.path.join(self.builddir, 'meson-logs')
        self.installdir = os.path.join(self.builddir, 'install')
        self.distdir = os.path.join(self.builddir, 'meson-dist')
        self.mtest_command = self.meson_command + ['test', '-C', self.builddir]
        self.builddirs.append(self.builddir)

    def new_builddir(self):
        # In case the directory is inside a symlinked directory, find the real
        # path otherwise we might not find the srcdir from inside the builddir.
        newdir = os.path.realpath(tempfile.mkdtemp())
        self.change_builddir(newdir)

    def _print_meson_log(self):
        log = os.path.join(self.logdir, 'meson-log.txt')
        if not os.path.isfile(log):
            print("{!r} doesn't exist".format(log))
            return
        with open(log, 'r', encoding='utf-8') as f:
            print(f.read())

    def tearDown(self):
        for path in self.builddirs:
            try:
                windows_proof_rmtree(path)
            except FileNotFoundError:
                pass
        os.environ.clear()
        os.environ.update(self.orig_env)
        super().tearDown()

    def _run(self, command, workdir=None):
        '''
        Run a command while printing the stdout and stderr to stdout,
        and also return a copy of it
        '''
        # If this call hangs CI will just abort. It is very hard to distinguish
        # between CI issue and test bug in that case. Set timeout and fail loud
        # instead.
        p = subprocess.run(command, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, env=os.environ.copy(),
                           universal_newlines=True, cwd=workdir, timeout=60 * 5)
        print(p.stdout)
        if p.returncode != 0:
            if 'MESON_SKIP_TEST' in p.stdout:
                raise unittest.SkipTest('Project requested skipping.')
            raise subprocess.CalledProcessError(p.returncode, command, output=p.stdout)
        return p.stdout

    def init(self, srcdir, extra_args=None, default_args=True, inprocess=False):
        self.assertPathExists(srcdir)
        if extra_args is None:
            extra_args = []
        if not isinstance(extra_args, list):
            extra_args = [extra_args]
        args = [srcdir, self.builddir]
        if default_args:
            args += ['--prefix', self.prefix,
                     '--libdir', self.libdir]
            if self.meson_cross_file:
                args += ['--cross-file', self.meson_cross_file]
        self.privatedir = os.path.join(self.builddir, 'meson-private')
        if inprocess:
            try:
                (returncode, out, err) = run_configure_inprocess(self.meson_args + args + extra_args)
                if 'MESON_SKIP_TEST' in out:
                    raise unittest.SkipTest('Project requested skipping.')
                if returncode != 0:
                    self._print_meson_log()
                    print('Stdout:\n')
                    print(out)
                    print('Stderr:\n')
                    print(err)
                    raise RuntimeError('Configure failed')
            except:
                self._print_meson_log()
                raise
            finally:
                # Close log file to satisfy Windows file locking
                mesonbuild.mlog.shutdown()
                mesonbuild.mlog.log_dir = None
                mesonbuild.mlog.log_file = None
        else:
            try:
                out = self._run(self.setup_command + args + extra_args)
            except unittest.SkipTest:
                raise unittest.SkipTest('Project requested skipping: ' + srcdir)
            except:
                self._print_meson_log()
                raise
        return out

    def build(self, target=None, extra_args=None):
        if extra_args is None:
            extra_args = []
        # Add arguments for building the target (if specified),
        # and using the build dir (if required, with VS)
        args = get_builddir_target_args(self.backend, self.builddir, target)
        return self._run(self.build_command + args + extra_args, workdir=self.builddir)

    def clean(self):
        dir_args = get_builddir_target_args(self.backend, self.builddir, None)
        self._run(self.clean_command + dir_args, workdir=self.builddir)

    def run_tests(self):
        self._run(self.test_command, workdir=self.builddir)

    def install(self, *, use_destdir=True):
        if self.backend is not Backend.ninja:
            raise unittest.SkipTest('{!r} backend can\'t install files'.format(self.backend.name))
        if use_destdir:
            os.environ['DESTDIR'] = self.installdir
        self._run(self.install_command, workdir=self.builddir)

    def uninstall(self):
        self._run(self.uninstall_command, workdir=self.builddir)

    def run_target(self, target):
        '''
        Run a Ninja target while printing the stdout and stderr to stdout,
        and also return a copy of it
        '''
        return self.build(target=target)

    def setconf(self, arg, will_build=True):
        if not isinstance(arg, list):
            arg = [arg]
        if will_build:
            ensure_backend_detects_changes(self.backend)
        self._run(self.mconf_command + arg + [self.builddir])

    def wipe(self):
        windows_proof_rmtree(self.builddir)

    def utime(self, f):
        ensure_backend_detects_changes(self.backend)
        os.utime(f)

    def get_compdb(self):
        if self.backend is not Backend.ninja:
            raise unittest.SkipTest('Compiler db not available with {} backend'.format(self.backend.name))
        with open(os.path.join(self.builddir, 'compile_commands.json')) as ifile:
            contents = json.load(ifile)
        # If Ninja is using .rsp files, generate them, read their contents, and
        # replace it as the command for all compile commands in the parsed json.
        if len(contents) > 0 and contents[0]['command'].endswith('.rsp'):
            # Pretend to build so that the rsp files are generated
            self.build(extra_args=['-d', 'keeprsp', '-n'])
            for each in contents:
                # Extract the actual command from the rsp file
                compiler, rsp = each['command'].split(' @')
                rsp = os.path.join(self.builddir, rsp)
                # Replace the command with its contents
                with open(rsp, 'r', encoding='utf-8') as f:
                    each['command'] = compiler + ' ' + f.read()
        return contents

    def get_meson_log(self):
        with open(os.path.join(self.builddir, 'meson-logs', 'meson-log.txt')) as f:
            return f.readlines()

    def get_meson_log_compiler_checks(self):
        '''
        Fetch a list command-lines run by meson for compiler checks.
        Each command-line is returned as a list of arguments.
        '''
        log = self.get_meson_log()
        prefix = 'Command line:'
        cmds = [l[len(prefix):].split() for l in log if l.startswith(prefix)]
        return cmds

    def introspect(self, args):
        if isinstance(args, str):
            args = [args]
        out = subprocess.check_output(self.mintro_command + args + [self.builddir],
                                      universal_newlines=True)
        return json.loads(out)

    def assertPathEqual(self, path1, path2):
        '''
        Handles a lot of platform-specific quirks related to paths such as
        separator, case-sensitivity, etc.
        '''
        self.assertEqual(PurePath(path1), PurePath(path2))

    def assertPathBasenameEqual(self, path, basename):
        msg = '{!r} does not end with {!r}'.format(path, basename)
        # We cannot use os.path.basename because it returns '' when the path
        # ends with '/' for some silly reason. This is not how the UNIX utility
        # `basename` works.
        path_basename = PurePath(path).parts[-1]
        self.assertEqual(PurePath(path_basename), PurePath(basename), msg)

    def assertBuildIsNoop(self):
        ret = self.build()
        if self.backend is Backend.ninja:
            self.assertEqual(ret.split('\n')[-2], self.no_rebuild_stdout)
        elif self.backend is Backend.vs:
            # Ensure that some target said that no rebuild was done
            self.assertIn('CustomBuild:\n  All outputs are up-to-date.', ret)
            self.assertIn('ClCompile:\n  All outputs are up-to-date.', ret)
            self.assertIn('Link:\n  All outputs are up-to-date.', ret)
            # Ensure that no targets were built
            clre = re.compile('ClCompile:\n [^\n]*cl', flags=re.IGNORECASE)
            linkre = re.compile('Link:\n [^\n]*link', flags=re.IGNORECASE)
            self.assertNotRegex(ret, clre)
            self.assertNotRegex(ret, linkre)
        elif self.backend is Backend.xcode:
            raise unittest.SkipTest('Please help us fix this test on the xcode backend')
        else:
            raise RuntimeError('Invalid backend: {!r}'.format(self.backend.name))

    def assertRebuiltTarget(self, target):
        ret = self.build()
        if self.backend is Backend.ninja:
            self.assertIn('Linking target {}'.format(target), ret)
        elif self.backend is Backend.vs:
            # Ensure that this target was rebuilt
            linkre = re.compile('Link:\n [^\n]*link[^\n]*' + target, flags=re.IGNORECASE)
            self.assertRegex(ret, linkre)
        elif self.backend is Backend.xcode:
            raise unittest.SkipTest('Please help us fix this test on the xcode backend')
        else:
            raise RuntimeError('Invalid backend: {!r}'.format(self.backend.name))

    def assertPathExists(self, path):
        m = 'Path {!r} should exist'.format(path)
        self.assertTrue(os.path.exists(path), msg=m)

    def assertPathDoesNotExist(self, path):
        m = 'Path {!r} should not exist'.format(path)
        self.assertFalse(os.path.exists(path), msg=m)


class AllPlatformTests(BasePlatformTests):
    '''
    Tests that should run on all platforms
    '''
    def test_default_options_prefix(self):
        '''
        Tests that setting a prefix in default_options in project() works.
        Can't be an ordinary test because we pass --prefix to meson there.
        https://github.com/mesonbuild/meson/issues/1349
        '''
        testdir = os.path.join(self.common_test_dir, '94 default options')
        self.init(testdir, default_args=False)
        opts = self.introspect('--buildoptions')
        for opt in opts:
            if opt['name'] == 'prefix':
                prefix = opt['value']
        self.assertEqual(prefix, '/absoluteprefix')

    def test_absolute_prefix_libdir(self):
        '''
        Tests that setting absolute paths for --prefix and --libdir work. Can't
        be an ordinary test because these are set via the command-line.
        https://github.com/mesonbuild/meson/issues/1341
        https://github.com/mesonbuild/meson/issues/1345
        '''
        testdir = os.path.join(self.common_test_dir, '94 default options')
        prefix = '/someabs'
        libdir = 'libdir'
        extra_args = ['--prefix=' + prefix,
                      # This can just be a relative path, but we want to test
                      # that passing this as an absolute path also works
                      '--libdir=' + prefix + '/' + libdir]
        self.init(testdir, extra_args, default_args=False)
        opts = self.introspect('--buildoptions')
        for opt in opts:
            if opt['name'] == 'prefix':
                self.assertEqual(prefix, opt['value'])
            elif opt['name'] == 'libdir':
                self.assertEqual(libdir, opt['value'])

    def test_libdir_must_be_inside_prefix(self):
        '''
        Tests that libdir is forced to be inside prefix no matter how it is set.
        Must be a unit test for obvious reasons.
        '''
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        # libdir being inside prefix is ok
        args = ['--prefix', '/opt', '--libdir', '/opt/lib32']
        self.init(testdir, args)
        self.wipe()
        # libdir not being inside prefix is not ok
        args = ['--prefix', '/usr', '--libdir', '/opt/lib32']
        self.assertRaises(subprocess.CalledProcessError, self.init, testdir, args)
        self.wipe()
        # libdir must be inside prefix even when set via mesonconf
        self.init(testdir)
        self.assertRaises(subprocess.CalledProcessError, self.setconf, '-Dlibdir=/opt', False)

    def test_prefix_dependent_defaults(self):
        '''
        Tests that configured directory paths are set to prefix dependent
        defaults.
        '''
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        expected = {
            '/opt': {'prefix': '/opt',
                     'bindir': 'bin', 'datadir': 'share', 'includedir': 'include',
                     'infodir': 'share/info',
                     'libexecdir': 'libexec', 'localedir': 'share/locale',
                     'localstatedir': 'var', 'mandir': 'share/man',
                     'sbindir': 'sbin', 'sharedstatedir': 'com',
                     'sysconfdir': 'etc'},
            '/usr': {'prefix': '/usr',
                     'bindir': 'bin', 'datadir': 'share', 'includedir': 'include',
                     'infodir': 'share/info',
                     'libexecdir': 'libexec', 'localedir': 'share/locale',
                     'localstatedir': '/var', 'mandir': 'share/man',
                     'sbindir': 'sbin', 'sharedstatedir': '/var/lib',
                     'sysconfdir': '/etc'},
            '/usr/local': {'prefix': '/usr/local',
                           'bindir': 'bin', 'datadir': 'share',
                           'includedir': 'include', 'infodir': 'share/info',
                           'libexecdir': 'libexec',
                           'localedir': 'share/locale',
                           'localstatedir': '/var/local', 'mandir': 'share/man',
                           'sbindir': 'sbin', 'sharedstatedir': '/var/local/lib',
                           'sysconfdir': 'etc'},
            # N.B. We don't check 'libdir' as it's platform dependent, see
            # default_libdir():
        }
        for prefix in expected:
            args = ['--prefix', prefix]
            self.init(testdir, args, default_args=False)
            opts = self.introspect('--buildoptions')
            for opt in opts:
                name = opt['name']
                value = opt['value']
                if name in expected[prefix]:
                    self.assertEqual(value, expected[prefix][name])
            self.wipe()

    def test_default_options_prefix_dependent_defaults(self):
        '''
        Tests that setting a prefix in default_options in project() sets prefix
        dependent defaults for other options, and that those defaults can
        be overridden in default_options or by the command line.
        '''
        testdir = os.path.join(self.common_test_dir, '173 default options prefix dependent defaults')
        expected = {
            '':
            {'prefix':         '/usr',
             'sysconfdir':     '/etc',
             'localstatedir':  '/var',
             'sharedstatedir': '/sharedstate'},
            '--prefix=/usr':
            {'prefix':         '/usr',
             'sysconfdir':     '/etc',
             'localstatedir':  '/var',
             'sharedstatedir': '/sharedstate'},
            '--sharedstatedir=/var/state':
            {'prefix':         '/usr',
             'sysconfdir':     '/etc',
             'localstatedir':  '/var',
             'sharedstatedir': '/var/state'},
            '--sharedstatedir=/var/state --prefix=/usr --sysconfdir=sysconf':
            {'prefix':         '/usr',
             'sysconfdir':     'sysconf',
             'localstatedir':  '/var',
             'sharedstatedir': '/var/state'},
        }
        for args in expected:
            self.init(testdir, args.split(), default_args=False)
            opts = self.introspect('--buildoptions')
            for opt in opts:
                name = opt['name']
                value = opt['value']
                if name in expected[args]:
                    self.assertEqual(value, expected[args][name])
            self.wipe()

    def test_static_library_overwrite(self):
        '''
        Tests that static libraries are never appended to, always overwritten.
        Has to be a unit test because this involves building a project,
        reconfiguring, and building it again so that `ar` is run twice on the
        same static library.
        https://github.com/mesonbuild/meson/issues/1355
        '''
        testdir = os.path.join(self.common_test_dir, '3 static')
        env = Environment(testdir, self.builddir, get_fake_options(self.prefix))
        cc = env.detect_c_compiler(False)
        static_linker = env.detect_static_linker(cc)
        if is_windows():
            raise unittest.SkipTest('https://github.com/mesonbuild/meson/issues/1526')
        if not isinstance(static_linker, mesonbuild.linkers.ArLinker):
            raise unittest.SkipTest('static linker is not `ar`')
        # Configure
        self.init(testdir)
        # Get name of static library
        targets = self.introspect('--targets')
        self.assertEqual(len(targets), 1)
        libname = targets[0]['filename']
        # Build and get contents of static library
        self.build()
        before = self._run(['ar', 't', os.path.join(self.builddir, libname)]).split()
        # Filter out non-object-file contents
        before = [f for f in before if f.endswith(('.o', '.obj'))]
        # Static library should contain only one object
        self.assertEqual(len(before), 1, msg=before)
        # Change the source to be built into the static library
        self.setconf('-Dsource=libfile2.c')
        self.build()
        after = self._run(['ar', 't', os.path.join(self.builddir, libname)]).split()
        # Filter out non-object-file contents
        after = [f for f in after if f.endswith(('.o', '.obj'))]
        # Static library should contain only one object
        self.assertEqual(len(after), 1, msg=after)
        # and the object must have changed
        self.assertNotEqual(before, after)

    def test_static_compile_order(self):
        '''
        Test that the order of files in a compiler command-line while compiling
        and linking statically is deterministic. This can't be an ordinary test
        case because we need to inspect the compiler database.
        https://github.com/mesonbuild/meson/pull/951
        '''
        testdir = os.path.join(self.common_test_dir, '5 linkstatic')
        self.init(testdir)
        compdb = self.get_compdb()
        # Rules will get written out in this order
        self.assertTrue(compdb[0]['file'].endswith("libfile.c"))
        self.assertTrue(compdb[1]['file'].endswith("libfile2.c"))
        self.assertTrue(compdb[2]['file'].endswith("libfile3.c"))
        self.assertTrue(compdb[3]['file'].endswith("libfile4.c"))
        # FIXME: We don't have access to the linker command

    def test_run_target_files_path(self):
        '''
        Test that run_targets are run from the correct directory
        https://github.com/mesonbuild/meson/issues/957
        '''
        testdir = os.path.join(self.common_test_dir, '58 run target')
        self.init(testdir)
        self.run_target('check_exists')

    def test_install_introspection(self):
        '''
        Tests that the Meson introspection API exposes install filenames correctly
        https://github.com/mesonbuild/meson/issues/829
        '''
        if self.backend is not Backend.ninja:
            raise unittest.SkipTest('{!r} backend can\'t install files'.format(self.backend.name))
        testdir = os.path.join(self.common_test_dir, '8 install')
        self.init(testdir)
        intro = self.introspect('--targets')
        if intro[0]['type'] == 'executable':
            intro = intro[::-1]
        self.assertPathEqual(intro[0]['install_filename'], '/usr/lib/libstat.a')
        self.assertPathEqual(intro[1]['install_filename'], '/usr/bin/prog' + exe_suffix)

    def test_uninstall(self):
        exename = os.path.join(self.installdir, 'usr/bin/prog' + exe_suffix)
        testdir = os.path.join(self.common_test_dir, '8 install')
        self.init(testdir)
        self.assertPathDoesNotExist(exename)
        self.install()
        self.assertPathExists(exename)
        self.uninstall()
        self.assertPathDoesNotExist(exename)

    def test_forcefallback(self):
        testdir = os.path.join(self.unit_test_dir, '27 forcefallback')
        self.init(testdir, ['--wrap-mode=forcefallback'])
        self.build()
        self.run_tests()

    def test_testsetups(self):
        if not shutil.which('valgrind'):
                raise unittest.SkipTest('Valgrind not installed.')
        testdir = os.path.join(self.unit_test_dir, '2 testsetups')
        self.init(testdir)
        self.build()
        # Run tests without setup
        self.run_tests()
        with open(os.path.join(self.logdir, 'testlog.txt')) as f:
            basic_log = f.read()
        # Run buggy test with setup that has env that will make it fail
        self.assertRaises(subprocess.CalledProcessError,
                          self._run, self.mtest_command + ['--setup=valgrind'])
        with open(os.path.join(self.logdir, 'testlog-valgrind.txt')) as f:
            vg_log = f.read()
        self.assertFalse('TEST_ENV is set' in basic_log)
        self.assertFalse('Memcheck' in basic_log)
        self.assertTrue('TEST_ENV is set' in vg_log)
        self.assertTrue('Memcheck' in vg_log)
        # Run buggy test with setup without env that will pass
        self._run(self.mtest_command + ['--setup=wrapper'])
        # Setup with no properties works
        self._run(self.mtest_command + ['--setup=empty'])
        # Setup with only env works
        self._run(self.mtest_command + ['--setup=onlyenv'])
        self._run(self.mtest_command + ['--setup=onlyenv2'])
        self._run(self.mtest_command + ['--setup=onlyenv3'])
        # Setup with only a timeout works
        self._run(self.mtest_command + ['--setup=timeout'])

    def test_testsetup_selection(self):
        testdir = os.path.join(self.unit_test_dir, '13 testsetup selection')
        self.init(testdir)
        self.build()

        # Run tests without setup
        self.run_tests()

        self.assertRaises(subprocess.CalledProcessError, self._run, self.mtest_command + ['--setup=missingfromfoo'])
        self._run(self.mtest_command + ['--setup=missingfromfoo', '--no-suite=foo:'])

        self._run(self.mtest_command + ['--setup=worksforall'])
        self._run(self.mtest_command + ['--setup=main:worksforall'])

        self.assertRaises(subprocess.CalledProcessError, self._run,
                          self.mtest_command + ['--setup=onlyinbar'])
        self.assertRaises(subprocess.CalledProcessError, self._run,
                          self.mtest_command + ['--setup=onlyinbar', '--no-suite=main:'])
        self._run(self.mtest_command + ['--setup=onlyinbar', '--no-suite=main:', '--no-suite=foo:'])
        self._run(self.mtest_command + ['--setup=bar:onlyinbar'])
        self.assertRaises(subprocess.CalledProcessError, self._run,
                          self.mtest_command + ['--setup=foo:onlyinbar'])
        self.assertRaises(subprocess.CalledProcessError, self._run,
                          self.mtest_command + ['--setup=main:onlyinbar'])

    def assertFailedTestCount(self, failure_count, command):
        try:
            self._run(command)
            self.assertEqual(0, failure_count, 'Expected %d tests to fail.' % failure_count)
        except subprocess.CalledProcessError as e:
            self.assertEqual(e.returncode, failure_count)

    def test_suite_selection(self):
        testdir = os.path.join(self.unit_test_dir, '4 suite selection')
        self.init(testdir)
        self.build()

        self.assertFailedTestCount(3, self.mtest_command)

        self.assertFailedTestCount(0, self.mtest_command + ['--suite', ':success'])
        self.assertFailedTestCount(3, self.mtest_command + ['--suite', ':fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', ':success'])
        self.assertFailedTestCount(0, self.mtest_command + ['--no-suite', ':fail'])

        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'mainprj'])
        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'subprjsucc'])
        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'subprjfail'])
        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'subprjmix'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'mainprj'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'subprjsucc'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'subprjfail'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'subprjmix'])

        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'mainprj:fail'])
        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'mainprj:success'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'mainprj:fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'mainprj:success'])

        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'subprjfail:fail'])
        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'subprjfail:success'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'subprjfail:fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'subprjfail:success'])

        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'subprjsucc:fail'])
        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'subprjsucc:success'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'subprjsucc:fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'subprjsucc:success'])

        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'subprjmix:fail'])
        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'subprjmix:success'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'subprjmix:fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'subprjmix:success'])

        self.assertFailedTestCount(2, self.mtest_command + ['--suite', 'subprjfail', '--suite', 'subprjmix:fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--suite', 'subprjfail', '--suite', 'subprjmix', '--suite', 'mainprj'])
        self.assertFailedTestCount(2, self.mtest_command + ['--suite', 'subprjfail', '--suite', 'subprjmix', '--suite', 'mainprj', '--no-suite', 'subprjmix:fail'])
        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'subprjfail', '--suite', 'subprjmix', '--suite', 'mainprj', '--no-suite', 'subprjmix:fail', 'mainprj-failing_test'])

        self.assertFailedTestCount(1, self.mtest_command + ['--no-suite', 'subprjfail:fail', '--no-suite', 'subprjmix:fail'])

    def test_build_by_default(self):
        testdir = os.path.join(self.common_test_dir, '137 build by default')
        self.init(testdir)
        self.build()
        genfile1 = os.path.join(self.builddir, 'generated1.dat')
        genfile2 = os.path.join(self.builddir, 'generated2.dat')
        exe1 = os.path.join(self.builddir, 'fooprog' + exe_suffix)
        exe2 = os.path.join(self.builddir, 'barprog' + exe_suffix)
        self.assertPathExists(genfile1)
        self.assertPathExists(genfile2)
        self.assertPathDoesNotExist(exe1)
        self.assertPathDoesNotExist(exe2)
        self.build(target=('fooprog' + exe_suffix))
        self.assertPathExists(exe1)
        self.build(target=('barprog' + exe_suffix))
        self.assertPathExists(exe2)

    def test_internal_include_order(self):
        testdir = os.path.join(self.common_test_dir, '138 include order')
        self.init(testdir)
        execmd = fxecmd = None
        for cmd in self.get_compdb():
            if 'someexe' in cmd['command']:
                execmd = cmd['command']
                continue
            if 'somefxe' in cmd['command']:
                fxecmd = cmd['command']
                continue
        if not execmd or not fxecmd:
            raise Exception('Could not find someexe and somfxe commands')
        # Check include order for 'someexe'
        incs = [a for a in shlex.split(execmd) if a.startswith("-I")]
        self.assertEqual(len(incs), 9)
        # target private dir
        self.assertPathEqual(incs[0], "-Isub4/sub4@@someexe@exe")
        # target build subdir
        self.assertPathEqual(incs[1], "-Isub4")
        # target source subdir
        self.assertPathBasenameEqual(incs[2], 'sub4')
        # include paths added via per-target c_args: ['-I'...]
        self.assertPathBasenameEqual(incs[3], 'sub3')
        # target include_directories: build dir
        self.assertPathEqual(incs[4], "-Isub2")
        # target include_directories: source dir
        self.assertPathBasenameEqual(incs[5], 'sub2')
        # target internal dependency include_directories: build dir
        self.assertPathEqual(incs[6], "-Isub1")
        # target internal dependency include_directories: source dir
        self.assertPathBasenameEqual(incs[7], 'sub1')
        # custom target include dir
        self.assertPathEqual(incs[8], '-Ictsub')
        # Check include order for 'somefxe'
        incs = [a for a in shlex.split(fxecmd) if a.startswith('-I')]
        self.assertEqual(len(incs), 9)
        # target private dir
        self.assertPathEqual(incs[0], '-Isomefxe@exe')
        # target build dir
        self.assertPathEqual(incs[1], '-I.')
        # target source dir
        self.assertPathBasenameEqual(incs[2], os.path.basename(testdir))
        # target internal dependency correct include_directories: build dir
        self.assertPathEqual(incs[3], "-Isub4")
        # target internal dependency correct include_directories: source dir
        self.assertPathBasenameEqual(incs[4], 'sub4')
        # target internal dependency dep include_directories: build dir
        self.assertPathEqual(incs[5], "-Isub1")
        # target internal dependency dep include_directories: source dir
        self.assertPathBasenameEqual(incs[6], 'sub1')
        # target internal dependency wrong include_directories: build dir
        self.assertPathEqual(incs[7], "-Isub2")
        # target internal dependency wrong include_directories: source dir
        self.assertPathBasenameEqual(incs[8], 'sub2')

    def test_compiler_detection(self):
        '''
        Test that automatic compiler detection and setting from the environment
        both work just fine. This is needed because while running project tests
        and other unit tests, we always read CC/CXX/etc from the environment.
        '''
        gnu = mesonbuild.compilers.GnuCompiler
        clang = mesonbuild.compilers.ClangCompiler
        intel = mesonbuild.compilers.IntelCompiler
        msvc = mesonbuild.compilers.VisualStudioCCompiler
        ar = mesonbuild.linkers.ArLinker
        lib = mesonbuild.linkers.VisualStudioLinker
        langs = [('c', 'CC'), ('cpp', 'CXX')]
        if not is_windows():
            langs += [('objc', 'OBJC'), ('objcpp', 'OBJCXX')]
        testdir = os.path.join(self.unit_test_dir, '5 compiler detection')
        env = Environment(testdir, self.builddir, get_fake_options(self.prefix))
        for lang, evar in langs:
            # Detect with evar and do sanity checks on that
            if evar in os.environ:
                ecc = getattr(env, 'detect_{}_compiler'.format(lang))(False)
                self.assertTrue(ecc.version)
                elinker = env.detect_static_linker(ecc)
                # Pop it so we don't use it for the next detection
                evalue = os.environ.pop(evar)
                # Very rough/strict heuristics. Would never work for actual
                # compiler detection, but should be ok for the tests.
                ebase = os.path.basename(evalue)
                if ebase.startswith('g') or ebase.endswith(('-gcc', '-g++')):
                    self.assertIsInstance(ecc, gnu)
                    self.assertIsInstance(elinker, ar)
                elif 'clang' in ebase:
                    self.assertIsInstance(ecc, clang)
                    self.assertIsInstance(elinker, ar)
                elif ebase.startswith('ic'):
                    self.assertIsInstance(ecc, intel)
                    self.assertIsInstance(elinker, ar)
                elif ebase.startswith('cl'):
                    self.assertIsInstance(ecc, msvc)
                    self.assertIsInstance(elinker, lib)
                else:
                    raise AssertionError('Unknown compiler {!r}'.format(evalue))
                # Check that we actually used the evalue correctly as the compiler
                self.assertEqual(ecc.get_exelist(), shlex.split(evalue))
            # Do auto-detection of compiler based on platform, PATH, etc.
            cc = getattr(env, 'detect_{}_compiler'.format(lang))(False)
            self.assertTrue(cc.version)
            linker = env.detect_static_linker(cc)
            # Check compiler type
            if isinstance(cc, gnu):
                self.assertIsInstance(linker, ar)
                if is_osx():
                    self.assertEqual(cc.gcc_type, mesonbuild.compilers.GCC_OSX)
                elif is_windows():
                    self.assertEqual(cc.gcc_type, mesonbuild.compilers.GCC_MINGW)
                elif is_cygwin():
                    self.assertEqual(cc.gcc_type, mesonbuild.compilers.GCC_CYGWIN)
                else:
                    self.assertEqual(cc.gcc_type, mesonbuild.compilers.GCC_STANDARD)
            if isinstance(cc, clang):
                self.assertIsInstance(linker, ar)
                if is_osx():
                    self.assertEqual(cc.clang_type, mesonbuild.compilers.CLANG_OSX)
                elif is_windows():
                    # Not implemented yet
                    self.assertEqual(cc.clang_type, mesonbuild.compilers.CLANG_WIN)
                else:
                    self.assertEqual(cc.clang_type, mesonbuild.compilers.CLANG_STANDARD)
            if isinstance(cc, intel):
                self.assertIsInstance(linker, ar)
                if is_osx():
                    self.assertEqual(cc.icc_type, mesonbuild.compilers.ICC_OSX)
                elif is_windows():
                    self.assertEqual(cc.icc_type, mesonbuild.compilers.ICC_WIN)
                else:
                    self.assertEqual(cc.icc_type, mesonbuild.compilers.ICC_STANDARD)
            if isinstance(cc, msvc):
                self.assertTrue(is_windows())
                self.assertIsInstance(linker, lib)
                self.assertEqual(cc.id, 'msvc')
                self.assertTrue(hasattr(cc, 'is_64'))
                # If we're in the appveyor CI, we know what the compiler will be
                if 'arch' in os.environ:
                    if os.environ['arch'] == 'x64':
                        self.assertTrue(cc.is_64)
                    else:
                        self.assertFalse(cc.is_64)
            # Set evar ourselves to a wrapper script that just calls the same
            # exelist + some argument. This is meant to test that setting
            # something like `ccache gcc -pipe` or `distcc ccache gcc` works.
            wrapper = os.path.join(testdir, 'compiler wrapper.py')
            wrappercc = python_command + [wrapper] + cc.get_exelist() + ['-DSOME_ARG']
            wrappercc_s = ''
            for w in wrappercc:
                wrappercc_s += shlex.quote(w) + ' '
            os.environ[evar] = wrappercc_s
            wcc = getattr(env, 'detect_{}_compiler'.format(lang))(False)
            # Check static linker too
            wrapperlinker = python_command + [wrapper] + linker.get_exelist() + linker.get_always_args()
            wrapperlinker_s = ''
            for w in wrapperlinker:
                wrapperlinker_s += shlex.quote(w) + ' '
            os.environ['AR'] = wrapperlinker_s
            wlinker = env.detect_static_linker(wcc)
            # Must be the same type since it's a wrapper around the same exelist
            self.assertIs(type(cc), type(wcc))
            self.assertIs(type(linker), type(wlinker))
            # Ensure that the exelist is correct
            self.assertEqual(wcc.get_exelist(), wrappercc)
            self.assertEqual(wlinker.get_exelist(), wrapperlinker)
            # Ensure that the version detection worked correctly
            self.assertEqual(cc.version, wcc.version)
            if hasattr(cc, 'is_64'):
                self.assertEqual(cc.is_64, wcc.is_64)

    def test_always_prefer_c_compiler_for_asm(self):
        testdir = os.path.join(self.common_test_dir, '141 c cpp and asm')
        # Skip if building with MSVC
        env = Environment(testdir, self.builddir, get_fake_options(self.prefix))
        if env.detect_c_compiler(False).get_id() == 'msvc':
            raise unittest.SkipTest('MSVC can\'t compile assembly')
        self.init(testdir)
        commands = {'c-asm': {}, 'cpp-asm': {}, 'cpp-c-asm': {}, 'c-cpp-asm': {}}
        for cmd in self.get_compdb():
            # Get compiler
            split = shlex.split(cmd['command'])
            if split[0] == 'ccache':
                compiler = split[1]
            else:
                compiler = split[0]
            # Classify commands
            if 'Ic-asm' in cmd['command']:
                if cmd['file'].endswith('.S'):
                    commands['c-asm']['asm'] = compiler
                elif cmd['file'].endswith('.c'):
                    commands['c-asm']['c'] = compiler
                else:
                    raise AssertionError('{!r} found in cpp-asm?'.format(cmd['command']))
            elif 'Icpp-asm' in cmd['command']:
                if cmd['file'].endswith('.S'):
                    commands['cpp-asm']['asm'] = compiler
                elif cmd['file'].endswith('.cpp'):
                    commands['cpp-asm']['cpp'] = compiler
                else:
                    raise AssertionError('{!r} found in cpp-asm?'.format(cmd['command']))
            elif 'Ic-cpp-asm' in cmd['command']:
                if cmd['file'].endswith('.S'):
                    commands['c-cpp-asm']['asm'] = compiler
                elif cmd['file'].endswith('.c'):
                    commands['c-cpp-asm']['c'] = compiler
                elif cmd['file'].endswith('.cpp'):
                    commands['c-cpp-asm']['cpp'] = compiler
                else:
                    raise AssertionError('{!r} found in c-cpp-asm?'.format(cmd['command']))
            elif 'Icpp-c-asm' in cmd['command']:
                if cmd['file'].endswith('.S'):
                    commands['cpp-c-asm']['asm'] = compiler
                elif cmd['file'].endswith('.c'):
                    commands['cpp-c-asm']['c'] = compiler
                elif cmd['file'].endswith('.cpp'):
                    commands['cpp-c-asm']['cpp'] = compiler
                else:
                    raise AssertionError('{!r} found in cpp-c-asm?'.format(cmd['command']))
            else:
                raise AssertionError('Unknown command {!r} found'.format(cmd['command']))
        # Check that .S files are always built with the C compiler
        self.assertEqual(commands['c-asm']['asm'], commands['c-asm']['c'])
        self.assertEqual(commands['c-asm']['asm'], commands['cpp-asm']['asm'])
        self.assertEqual(commands['cpp-asm']['asm'], commands['c-cpp-asm']['c'])
        self.assertEqual(commands['c-cpp-asm']['asm'], commands['c-cpp-asm']['c'])
        self.assertEqual(commands['cpp-c-asm']['asm'], commands['cpp-c-asm']['c'])
        self.assertNotEqual(commands['cpp-asm']['asm'], commands['cpp-asm']['cpp'])
        self.assertNotEqual(commands['c-cpp-asm']['c'], commands['c-cpp-asm']['cpp'])
        self.assertNotEqual(commands['cpp-c-asm']['c'], commands['cpp-c-asm']['cpp'])
        # Check that the c-asm target is always linked with the C linker
        build_ninja = os.path.join(self.builddir, 'build.ninja')
        with open(build_ninja, 'r', encoding='utf-8') as f:
            contents = f.read()
            m = re.search('build c-asm.*: c_LINKER', contents)
        self.assertIsNotNone(m, msg=contents)

    def test_preprocessor_checks_CPPFLAGS(self):
        '''
        Test that preprocessor compiler checks read CPPFLAGS but not CFLAGS
        '''
        testdir = os.path.join(self.common_test_dir, '140 get define')
        define = 'MESON_TEST_DEFINE_VALUE'
        # NOTE: this list can't have \n, ' or "
        # \n is never substituted by the GNU pre-processor via a -D define
        # ' and " confuse shlex.split() even when they are escaped
        # % and # confuse the MSVC preprocessor
        # !, ^, *, and < confuse lcc preprocessor
        value = 'spaces and fun@$&()-=_+{}[]:;>?,./~`'
        os.environ['CPPFLAGS'] = '-D{}="{}"'.format(define, value)
        os.environ['CFLAGS'] = '-DMESON_FAIL_VALUE=cflags-read'.format(define)
        self.init(testdir, ['-D{}={}'.format(define, value)])

    def test_custom_target_exe_data_deterministic(self):
        testdir = os.path.join(self.common_test_dir, '117 custom target capture')
        self.init(testdir)
        meson_exe_dat1 = glob(os.path.join(self.privatedir, 'meson_exe*.dat'))
        self.wipe()
        self.init(testdir)
        meson_exe_dat2 = glob(os.path.join(self.privatedir, 'meson_exe*.dat'))
        self.assertListEqual(meson_exe_dat1, meson_exe_dat2)

    def test_source_changes_cause_rebuild(self):
        '''
        Test that changes to sources and headers cause rebuilds, but not
        changes to unused files (as determined by the dependency file) in the
        input files list.
        '''
        testdir = os.path.join(self.common_test_dir, '22 header in file list')
        self.init(testdir)
        self.build()
        # Immediately rebuilding should not do anything
        self.assertBuildIsNoop()
        # Changing mtime of header.h should rebuild everything
        self.utime(os.path.join(testdir, 'header.h'))
        self.assertRebuiltTarget('prog')

    def test_custom_target_changes_cause_rebuild(self):
        '''
        Test that in a custom target, changes to the input files, the
        ExternalProgram, and any File objects on the command-line cause
        a rebuild.
        '''
        testdir = os.path.join(self.common_test_dir, '64 custom header generator')
        self.init(testdir)
        self.build()
        # Immediately rebuilding should not do anything
        self.assertBuildIsNoop()
        # Changing mtime of these should rebuild everything
        for f in ('input.def', 'makeheader.py', 'somefile.txt'):
            self.utime(os.path.join(testdir, f))
            self.assertRebuiltTarget('prog')

    def test_static_library_lto(self):
        '''
        Test that static libraries can be built with LTO and linked to
        executables. On Linux, this requires the use of gcc-ar.
        https://github.com/mesonbuild/meson/issues/1646
        '''
        testdir = os.path.join(self.common_test_dir, '5 linkstatic')
        self.init(testdir, extra_args='-Db_lto=true')
        self.build()
        self.run_tests()

    def test_dist_git(self):
        if not shutil.which('git'):
            raise unittest.SkipTest('Git not found')

        def git_init(project_dir):
            subprocess.check_call(['git', 'init'], cwd=project_dir, stdout=subprocess.DEVNULL)
            subprocess.check_call(['git', 'config',
                                   'user.name', 'Author Person'], cwd=project_dir)
            subprocess.check_call(['git', 'config',
                                   'user.email', 'teh_coderz@example.com'], cwd=project_dir)
            subprocess.check_call(['git', 'add', 'meson.build', 'distexe.c'], cwd=project_dir,
                                  stdout=subprocess.DEVNULL)
            subprocess.check_call(['git', 'commit', '-a', '-m', 'I am a project'], cwd=project_dir,
                                  stdout=subprocess.DEVNULL)

        try:
            self.dist_impl(git_init)
        except PermissionError:
            # When run under Windows CI, something (virus scanner?)
            # holds on to the git files so cleaning up the dir
            # fails sometimes.
            pass

    def test_dist_hg(self):
        if not shutil.which('hg'):
            raise unittest.SkipTest('Mercurial not found')
        if self.backend is not Backend.ninja:
            raise unittest.SkipTest('Dist is only supported with Ninja')

        def hg_init(project_dir):
            subprocess.check_call(['hg', 'init'], cwd=project_dir)
            with open(os.path.join(project_dir, '.hg', 'hgrc'), 'w') as f:
                print('[ui]', file=f)
                print('username=Author Person <teh_coderz@example.com>', file=f)
            subprocess.check_call(['hg', 'add', 'meson.build', 'distexe.c'], cwd=project_dir)
            subprocess.check_call(['hg', 'commit', '-m', 'I am a project'], cwd=project_dir)

        try:
            self.dist_impl(hg_init)
        except PermissionError:
            # When run under Windows CI, something (virus scanner?)
            # holds on to the hg files so cleaning up the dir
            # fails sometimes.
            pass

    def dist_impl(self, vcs_init):
        # Create this on the fly because having rogue .git directories inside
        # the source tree leads to all kinds of trouble.
        with tempfile.TemporaryDirectory() as project_dir:
            with open(os.path.join(project_dir, 'meson.build'), 'w') as ofile:
                ofile.write('''project('disttest', 'c', version : '1.4.3')
e = executable('distexe', 'distexe.c')
test('dist test', e)
''')
            with open(os.path.join(project_dir, 'distexe.c'), 'w') as ofile:
                ofile.write('''#include<stdio.h>

int main(int argc, char **argv) {
    printf("I am a distribution test.\\n");
    return 0;
}
''')
            vcs_init(project_dir)
            self.init(project_dir)
            self.build('dist')
            distfile = os.path.join(self.distdir, 'disttest-1.4.3.tar.xz')
            checksumfile = distfile + '.sha256sum'
            self.assertPathExists(distfile)
            self.assertPathExists(checksumfile)

    def test_rpath_uses_ORIGIN(self):
        '''
        Test that built targets use $ORIGIN in rpath, which ensures that they
        are relocatable and ensures that builds are reproducible since the
        build directory won't get embedded into the built binaries.
        '''
        if is_windows() or is_cygwin():
            raise unittest.SkipTest('Windows PE/COFF binaries do not use RPATH')
        testdir = os.path.join(self.common_test_dir, '46 library chain')
        self.init(testdir)
        self.build()
        for each in ('prog', 'subdir/liblib1.so', ):
            rpath = get_rpath(os.path.join(self.builddir, each))
            self.assertTrue(rpath)
            if is_dragonflybsd():
                # DragonflyBSD will prepend /usr/lib/gccVERSION to the rpath,
                # so ignore that.
                self.assertTrue(rpath.startswith('/usr/lib/gcc'))
                rpaths = rpath.split(':')[1:]
            else:
                rpaths = rpath.split(':')
            for path in rpaths:
                self.assertTrue(path.startswith('$ORIGIN'), msg=(each, path))
        # These two don't link to anything else, so they do not need an rpath entry.
        for each in ('subdir/subdir2/liblib2.so', 'subdir/subdir3/liblib3.so'):
            rpath = get_rpath(os.path.join(self.builddir, each))
            if is_dragonflybsd():
                # The rpath should be equal to /usr/lib/gccVERSION
                self.assertTrue(rpath.startswith('/usr/lib/gcc'))
                self.assertEqual(len(rpath.split(':')), 1)
            else:
                self.assertTrue(rpath is None)

    def test_dash_d_dedup(self):
        testdir = os.path.join(self.unit_test_dir, '10 d dedup')
        self.init(testdir)
        cmd = self.get_compdb()[0]['command']
        self.assertTrue('-D FOO -D BAR' in cmd or
                        '"-D" "FOO" "-D" "BAR"' in cmd or
                        '/D FOO /D BAR' in cmd or
                        '"/D" "FOO" "/D" "BAR"' in cmd)

    def test_all_forbidden_targets_tested(self):
        '''
        Test that all forbidden targets are tested in the '159 reserved targets'
        test. Needs to be a unit test because it accesses Meson internals.
        '''
        testdir = os.path.join(self.common_test_dir, '159 reserved targets')
        targets = mesonbuild.coredata.forbidden_target_names
        # We don't actually define a target with this name
        targets.pop('build.ninja')
        # Remove this to avoid multiple entries with the same name
        # but different case.
        targets.pop('PHONY')
        for i in targets:
            self.assertPathExists(os.path.join(testdir, i))

    def detect_prebuild_env(self):
        env = Environment('', self.builddir, get_fake_options(self.prefix))
        cc = env.detect_c_compiler(False)
        stlinker = env.detect_static_linker(cc)
        if mesonbuild.mesonlib.is_windows():
            object_suffix = 'obj'
            shared_suffix = 'dll'
        elif mesonbuild.mesonlib.is_cygwin():
            object_suffix = 'o'
            shared_suffix = 'dll'
        elif mesonbuild.mesonlib.is_osx():
            object_suffix = 'o'
            shared_suffix = 'dylib'
        else:
            object_suffix = 'o'
            shared_suffix = 'so'
        return (cc, stlinker, object_suffix, shared_suffix)

    def pbcompile(self, compiler, source, objectfile, extra_args=[]):
        cmd = compiler.get_exelist()
        if compiler.id == 'msvc':
            cmd += ['/nologo', '/Fo' + objectfile, '/c', source] + extra_args
        else:
            cmd += ['-c', source, '-o', objectfile] + extra_args
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_prebuilt_object(self):
        (compiler, _, object_suffix, _) = self.detect_prebuild_env()
        tdir = os.path.join(self.unit_test_dir, '14 prebuilt object')
        source = os.path.join(tdir, 'source.c')
        objectfile = os.path.join(tdir, 'prebuilt.' + object_suffix)
        self.pbcompile(compiler, source, objectfile)
        try:
            self.init(tdir)
            self.build()
            self.run_tests()
        finally:
            os.unlink(objectfile)

    def build_static_lib(self, compiler, linker, source, objectfile, outfile, extra_args=None):
        if extra_args is None:
            extra_args = []
        if compiler.id == 'msvc':
            link_cmd = ['lib', '/NOLOGO', '/OUT:' + outfile, objectfile]
        else:
            link_cmd = ['ar', 'csr', outfile, objectfile]
        link_cmd = linker.get_exelist()
        link_cmd += linker.get_always_args()
        link_cmd += linker.get_std_link_args()
        link_cmd += linker.get_output_args(outfile)
        link_cmd += [objectfile]
        self.pbcompile(compiler, source, objectfile, extra_args=extra_args)
        try:
            subprocess.check_call(link_cmd)
        finally:
            os.unlink(objectfile)

    def test_prebuilt_static_lib(self):
        (cc, stlinker, object_suffix, _) = self.detect_prebuild_env()
        tdir = os.path.join(self.unit_test_dir, '15 prebuilt static')
        source = os.path.join(tdir, 'libdir/best.c')
        objectfile = os.path.join(tdir, 'libdir/best.' + object_suffix)
        stlibfile = os.path.join(tdir, 'libdir/libbest.a')
        self.build_static_lib(cc, stlinker, source, objectfile, stlibfile)
        # Run the test
        try:
            self.init(tdir)
            self.build()
            self.run_tests()
        finally:
            os.unlink(stlibfile)

    def build_shared_lib(self, compiler, source, objectfile, outfile, impfile, extra_args=None):
        if extra_args is None:
            extra_args = []
        if compiler.id == 'msvc':
            link_cmd = ['link', '/NOLOGO', '/DLL', '/DEBUG',
                        '/IMPLIB:' + impfile, '/OUT:' + outfile, objectfile]
        else:
            extra_args += ['-fPIC']
            link_cmd = compiler.get_exelist() + ['-shared', '-o', outfile, objectfile]
            if not mesonbuild.mesonlib.is_osx():
                link_cmd += ['-Wl,-soname=' + os.path.basename(outfile)]
        self.pbcompile(compiler, source, objectfile, extra_args=extra_args)
        try:
            subprocess.check_call(link_cmd)
        finally:
            os.unlink(objectfile)

    def test_prebuilt_shared_lib(self):
        (cc, _, object_suffix, shared_suffix) = self.detect_prebuild_env()
        tdir = os.path.join(self.unit_test_dir, '16 prebuilt shared')
        source = os.path.join(tdir, 'alexandria.c')
        objectfile = os.path.join(tdir, 'alexandria.' + object_suffix)
        impfile = os.path.join(tdir, 'alexandria.lib')
        if cc.id == 'msvc':
            shlibfile = os.path.join(tdir, 'alexandria.' + shared_suffix)
        elif is_cygwin():
            shlibfile = os.path.join(tdir, 'cygalexandria.' + shared_suffix)
        else:
            shlibfile = os.path.join(tdir, 'libalexandria.' + shared_suffix)
        self.build_shared_lib(cc, source, objectfile, shlibfile, impfile)
        # Run the test
        try:
            self.init(tdir)
            self.build()
            self.run_tests()
        finally:
            os.unlink(shlibfile)
            if mesonbuild.mesonlib.is_windows():
                # Clean up all the garbage MSVC writes in the
                # source tree.
                for fname in glob(os.path.join(tdir, 'alexandria.*')):
                    if os.path.splitext(fname)[1] not in ['.c', '.h']:
                        os.unlink(fname)

    @skipIfNoPkgconfig
    def test_pkgconfig_static(self):
        '''
        Test that the we prefer static libraries when `static: true` is
        passed to dependency() with pkg-config. Can't be an ordinary test
        because we need to build libs and try to find them from meson.build

        Also test that it's not a hard error to have unsatisfiable library deps
        since system libraries -lm will never be found statically.
        https://github.com/mesonbuild/meson/issues/2785
        '''
        (cc, stlinker, objext, shext) = self.detect_prebuild_env()
        testdir = os.path.join(self.unit_test_dir, '17 pkgconfig static')
        source = os.path.join(testdir, 'foo.c')
        objectfile = os.path.join(testdir, 'foo.' + objext)
        stlibfile = os.path.join(testdir, 'libfoo.a')
        impfile = os.path.join(testdir, 'foo.lib')
        if cc.id == 'msvc':
            shlibfile = os.path.join(testdir, 'foo.' + shext)
        elif is_cygwin():
            shlibfile = os.path.join(testdir, 'cygfoo.' + shext)
        else:
            shlibfile = os.path.join(testdir, 'libfoo.' + shext)
        # Build libs
        self.build_static_lib(cc, stlinker, source, objectfile, stlibfile, extra_args=['-DFOO_STATIC'])
        self.build_shared_lib(cc, source, objectfile, shlibfile, impfile)
        # Run test
        os.environ['PKG_CONFIG_LIBDIR'] = self.builddir
        try:
            self.init(testdir)
            self.build()
            self.run_tests()
        finally:
            os.unlink(stlibfile)
            os.unlink(shlibfile)
            if mesonbuild.mesonlib.is_windows():
                # Clean up all the garbage MSVC writes in the
                # source tree.
                for fname in glob(os.path.join(testdir, 'foo.*')):
                    if os.path.splitext(fname)[1] not in ['.c', '.h', '.in']:
                        os.unlink(fname)

    @skipIfNoPkgconfig
    def test_pkgconfig_gen_escaping(self):
        testdir = os.path.join(self.common_test_dir, '51 pkgconfig-gen')
        prefix = '/usr/with spaces'
        libdir = 'lib'
        self.init(testdir, extra_args=['--prefix=' + prefix,
                                       '--libdir=' + libdir])
        # Find foo dependency
        os.environ['PKG_CONFIG_LIBDIR'] = self.privatedir
        env = Environment(testdir, self.builddir, get_fake_options(self.prefix))
        kwargs = {'required': True, 'silent': True}
        foo_dep = PkgConfigDependency('libfoo', env, kwargs)
        # Ensure link_args are properly quoted
        libdir = PurePath(prefix) / PurePath(libdir)
        link_args = ['-L' + libdir.as_posix(), '-lfoo']
        self.assertEqual(foo_dep.get_link_args(), link_args)
        # Ensure include args are properly quoted
        incdir = PurePath(prefix) / PurePath('include')
        cargs = ['-I' + incdir.as_posix()]
        self.assertEqual(foo_dep.get_compile_args(), cargs)

    def test_array_option_change(self):
        def get_opt():
            opts = self.introspect('--buildoptions')
            for x in opts:
                if x.get('name') == 'list':
                    return x
            raise Exception(opts)

        expected = {
            'name': 'list',
            'description': 'list',
            'type': 'array',
            'value': ['foo', 'bar'],
        }
        tdir = os.path.join(self.unit_test_dir, '18 array option')
        self.init(tdir)
        original = get_opt()
        self.assertDictEqual(original, expected)

        expected['value'] = ['oink', 'boink']
        self.setconf('-Dlist=oink,boink')
        changed = get_opt()
        self.assertEqual(changed, expected)

    def test_array_option_bad_change(self):
        def get_opt():
            opts = self.introspect('--buildoptions')
            for x in opts:
                if x.get('name') == 'list':
                    return x
            raise Exception(opts)

        expected = {
            'name': 'list',
            'description': 'list',
            'type': 'array',
            'value': ['foo', 'bar'],
        }
        tdir = os.path.join(self.unit_test_dir, '18 array option')
        self.init(tdir)
        original = get_opt()
        self.assertDictEqual(original, expected)
        with self.assertRaises(subprocess.CalledProcessError):
            self.setconf('-Dlist=bad')
        changed = get_opt()
        self.assertDictEqual(changed, expected)

    def test_array_option_empty_equivalents(self):
        """Array options treat -Dopt=[] and -Dopt= as equivalent."""
        def get_opt():
            opts = self.introspect('--buildoptions')
            for x in opts:
                if x.get('name') == 'list':
                    return x
            raise Exception(opts)

        expected = {
            'name': 'list',
            'description': 'list',
            'type': 'array',
            'value': [],
        }
        tdir = os.path.join(self.unit_test_dir, '18 array option')
        self.init(tdir, extra_args='-Dlist=')
        original = get_opt()
        self.assertDictEqual(original, expected)

    def opt_has(self, name, value):
        res = self.introspect('--buildoptions')
        found = False
        for i in res:
            if i['name'] == name:
                self.assertEqual(i['value'], value)
                found = True
                break
        self.assertTrue(found, "Array option not found in introspect data.")

    def test_free_stringarray_setting(self):
        testdir = os.path.join(self.common_test_dir, '47 options')
        self.init(testdir)
        self.opt_has('free_array_opt', [])
        self.setconf('-Dfree_array_opt=foo,bar', will_build=False)
        self.opt_has('free_array_opt', ['foo', 'bar'])
        self.setconf("-Dfree_array_opt=['a,b', 'c,d']", will_build=False)
        self.opt_has('free_array_opt', ['a,b', 'c,d'])

    def test_subproject_promotion(self):
        testdir = os.path.join(self.unit_test_dir, '13 promote')
        workdir = os.path.join(self.builddir, 'work')
        shutil.copytree(testdir, workdir)
        spdir = os.path.join(workdir, 'subprojects')
        s3dir = os.path.join(spdir, 's3')
        scommondir = os.path.join(spdir, 'scommon')
        self.assertFalse(os.path.isdir(s3dir))
        subprocess.check_call(self.wrap_command + ['promote', 's3'], cwd=workdir)
        self.assertTrue(os.path.isdir(s3dir))
        self.assertFalse(os.path.isdir(scommondir))
        self.assertNotEqual(subprocess.call(self.wrap_command + ['promote', 'scommon'],
                                            cwd=workdir,
                                            stdout=subprocess.DEVNULL), 0)
        self.assertFalse(os.path.isdir(scommondir))
        subprocess.check_call(self.wrap_command + ['promote', 'subprojects/s2/subprojects/scommon'], cwd=workdir)
        self.assertTrue(os.path.isdir(scommondir))
        promoted_wrap = os.path.join(spdir, 'athing.wrap')
        self.assertFalse(os.path.isfile(promoted_wrap))
        subprocess.check_call(self.wrap_command + ['promote', 'athing'], cwd=workdir)
        self.assertTrue(os.path.isfile(promoted_wrap))
        self.init(workdir)
        self.build()

    def test_warning_location(self):
        tdir = os.path.join(self.unit_test_dir, '21 warning location')
        out = self.init(tdir)
        for expected in [
            r'meson.build:4: WARNING: Keyword argument "link_with" defined multiple times.',
            r'sub' + os.path.sep + r'meson.build:3: WARNING: Keyword argument "link_with" defined multiple times.',
            r'meson.build:6: WARNING: a warning of some sort',
            r'sub' + os.path.sep + r'meson.build:4: WARNING: subdir warning',
            r'meson.build:7: WARNING: Module unstable-simd has no backwards or forwards compatibility and might not exist in future releases.',
            r"meson.build:11: WARNING: The variable(s) 'MISSING' in the input file 'conf.in' are not present in the given configuration data.",
            r'meson.build:1: WARNING: Passed invalid keyword argument "invalid".',
        ]:
            self.assertRegex(out, re.escape(expected))

    def test_permitted_method_kwargs(self):
        tdir = os.path.join(self.unit_test_dir, '23 non-permitted kwargs')
        out = self.init(tdir)
        for expected in [
            r'WARNING: Passed invalid keyword argument "prefixxx".',
            r'WARNING: Passed invalid keyword argument "argsxx".',
            r'WARNING: Passed invalid keyword argument "invalidxx".',
        ]:
            self.assertRegex(out, re.escape(expected))

    def test_templates(self):
        ninja = detect_ninja()
        if ninja is None:
            raise unittest.SkipTest('This test currently requires ninja. Fix this once "meson build" works.')
        for lang in ('c', 'cpp'):
            for type in ('executable', 'library'):
                with tempfile.TemporaryDirectory() as tmpdir:
                    self._run(self.meson_command + ['init', '--language', lang, '--type', type],
                              workdir=tmpdir)
                    self._run(self.setup_command + ['--backend=ninja', 'builddir'],
                              workdir=tmpdir)
                    self._run(ninja,
                              workdir=os.path.join(tmpdir, 'builddir'))
            with tempfile.TemporaryDirectory() as tmpdir:
                with open(os.path.join(tmpdir, 'foo.' + lang), 'w') as f:
                    f.write('int main() {}')
                self._run(self.meson_command + ['init', '-b'], workdir=tmpdir)

    # The test uses mocking and thus requires that
    # the current process is the one to run the Meson steps.
    # If we are using an external test executable (most commonly
    # in Debian autopkgtests) then the mocking won't work.
    @unittest.skipIf('MESON_EXE' in os.environ, 'MESON_EXE is defined, can not use mocking.')
    def test_cross_file_system_paths(self):
        if is_windows():
            raise unittest.SkipTest('system crossfile paths not defined for Windows (yet)')

        testdir = os.path.join(self.common_test_dir, '1 trivial')
        cross_content = textwrap.dedent("""\
            [binaries]
            c = '/usr/bin/cc'
            ar = '/usr/bin/ar'
            strip = '/usr/bin/ar'

            [properties]

            [host_machine]
            system = 'linux'
            cpu_family = 'x86'
            cpu = 'i686'
            endian = 'little'
            """)

        with tempfile.TemporaryDirectory() as d:
            dir_ = os.path.join(d, 'meson', 'cross')
            os.makedirs(dir_)
            with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False) as f:
                f.write(cross_content)
            name = os.path.basename(f.name)

            with mock.patch.dict(os.environ, {'XDG_DATA_HOME': d}):
                self.init(testdir, ['--cross-file=' + name], inprocess=True)
                self.wipe()

            with mock.patch.dict(os.environ, {'XDG_DATA_DIRS': d}):
                os.environ.pop('XDG_DATA_HOME', None)
                self.init(testdir, ['--cross-file=' + name], inprocess=True)
                self.wipe()

        with tempfile.TemporaryDirectory() as d:
            dir_ = os.path.join(d, '.local', 'share', 'meson', 'cross')
            os.makedirs(dir_)
            with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False) as f:
                f.write(cross_content)
            name = os.path.basename(f.name)

            with mock.patch('mesonbuild.coredata.os.path.expanduser', lambda x: x.replace('~', d)):
                self.init(testdir, ['--cross-file=' + name], inprocess=True)
                self.wipe()

    def test_compiler_run_command(self):
        '''
        The test checks that the compiler object can be passed to
        run_command().
        '''
        testdir = os.path.join(self.unit_test_dir, '23 compiler run_command')
        self.init(testdir)

    def test_identical_target_name_in_subproject_flat_layout(self):
        '''
        Test that identical targets in different subprojects do not collide
        if layout is flat.
        '''
        testdir = os.path.join(self.common_test_dir, '182 identical target name in subproject flat layout')
        self.init(testdir, extra_args=['--layout=flat'])
        self.build()

    def test_identical_target_name_in_subdir_flat_layout(self):
        '''
        Test that identical targets in different subdirs do not collide
        if layout is flat.
        '''
        testdir = os.path.join(self.common_test_dir, '192 same target name flat layout')
        self.init(testdir, extra_args=['--layout=flat'])
        self.build()

    def test_flock(self):
        exception_raised = False
        with tempfile.TemporaryDirectory() as tdir:
            os.mkdir(os.path.join(tdir, 'meson-private'))
            with BuildDirLock(tdir):
                try:
                    with BuildDirLock(tdir):
                        pass
                except MesonException:
                    exception_raised = True
        self.assertTrue(exception_raised, 'Double locking did not raise exception.')

    @unittest.skipIf(is_osx(), 'Test not applicable to OSX')
    def test_check_module_linking(self):
        """
        Test that link_with: a shared module issues a warning
        https://github.com/mesonbuild/meson/issues/2865
        (That an error is raised on OSX is exercised by test failing/78)
        """
        tdir = os.path.join(self.unit_test_dir, '26 shared_mod linking')
        out = self.init(tdir)
        msg = ('''WARNING: target links against shared modules. This is not
recommended as it is not supported on some platforms''')
        self.assertIn(msg, out)

    def test_ndebug_if_release_disabled(self):
        testdir = os.path.join(self.unit_test_dir, '25 ndebug if-release')
        self.init(testdir, extra_args=['--buildtype=release', '-Db_ndebug=if-release'])
        self.build()
        exe = os.path.join(self.builddir, 'main')
        self.assertEqual(b'NDEBUG=1', subprocess.check_output(exe).strip())

    def test_ndebug_if_release_enabled(self):
        testdir = os.path.join(self.unit_test_dir, '25 ndebug if-release')
        self.init(testdir, extra_args=['--buildtype=debugoptimized', '-Db_ndebug=if-release'])
        self.build()
        exe = os.path.join(self.builddir, 'main')
        self.assertEqual(b'NDEBUG=0', subprocess.check_output(exe).strip())

    def test_guessed_linker_dependencies(self):
        '''
        Test that meson adds dependencies for libraries based on the final
        linker command line.
        '''
        # build library
        testdirbase = os.path.join(self.unit_test_dir, '26 guessed linker dependencies')
        testdirlib = os.path.join(testdirbase, 'lib')
        extra_args = None
        env = Environment(testdirlib, self.builddir, get_fake_options(self.prefix))
        if env.detect_c_compiler(False).get_id() != 'msvc':
            # static libraries are not linkable with -l with msvc because meson installs them
            # as .a files which unix_args_to_native will not know as it expects libraries to use
            # .lib as extension. For a DLL the import library is installed as .lib. Thus for msvc
            # this tests needs to use shared libraries to test the path resolving logic in the
            # dependency generation code path.
            extra_args = ['--default-library', 'static']
        self.init(testdirlib, extra_args=extra_args)
        self.build()
        self.install()
        libbuilddir = self.builddir
        installdir = self.installdir
        libdir = os.path.join(self.installdir, self.prefix.lstrip('/').lstrip('\\'), 'lib')

        # build user of library
        self.new_builddir()
        # replace is needed because meson mangles platform pathes passed via LDFLAGS
        os.environ["LDFLAGS"] = '-L{}'.format(libdir.replace('\\', '/'))
        self.init(os.path.join(testdirbase, 'exe'))
        del os.environ["LDFLAGS"]
        self.build()
        self.assertBuildIsNoop()

        # rebuild library
        exebuilddir = self.builddir
        self.installdir = installdir
        self.builddir = libbuilddir
        # Microsoft's compiler is quite smart about touching import libs on changes,
        # so ensure that there is actually a change in symbols.
        self.setconf('-Dmore_exports=true')
        self.build()
        self.install()
        # no ensure_backend_detects_changes needed because self.setconf did that already

        # assert user of library will be rebuild
        self.builddir = exebuilddir
        self.assertRebuiltTarget('app')

    def test_conflicting_d_dash_option(self):
        testdir = os.path.join(self.unit_test_dir, '30 mixed command line args')
        with self.assertRaises(subprocess.CalledProcessError) as e:
            self.init(testdir, extra_args=['-Dbindir=foo', '--bindir=bar'])
            # Just to ensure that we caught the correct error
            self.assertIn('passed as both', e.stderr)

    def _test_same_option_twice(self, arg, args):
        testdir = os.path.join(self.unit_test_dir, '30 mixed command line args')
        self.init(testdir, extra_args=args)
        opts = self.introspect('--buildoptions')
        for item in opts:
            if item['name'] == arg:
                self.assertEqual(item['value'], 'bar')
                return
        raise Exception('Missing {} value?'.format(arg))

    def test_same_dash_option_twice(self):
        self._test_same_option_twice('bindir', ['--bindir=foo', '--bindir=bar'])

    def test_same_d_option_twice(self):
        self._test_same_option_twice('bindir', ['-Dbindir=foo', '-Dbindir=bar'])

    def test_same_project_d_option_twice(self):
        self._test_same_option_twice('one', ['-Done=foo', '-Done=bar'])

    def _test_same_option_twice_configure(self, arg, args):
        testdir = os.path.join(self.unit_test_dir, '30 mixed command line args')
        self.init(testdir)
        self.setconf(args)
        opts = self.introspect('--buildoptions')
        for item in opts:
            if item['name'] == arg:
                self.assertEqual(item['value'], 'bar')
                return
        raise Exception('Missing {} value?'.format(arg))

    def test_same_dash_option_twice_configure(self):
        self._test_same_option_twice_configure(
            'bindir', ['--bindir=foo', '--bindir=bar'])

    def test_same_d_option_twice_configure(self):
        self._test_same_option_twice_configure(
            'bindir', ['-Dbindir=foo', '-Dbindir=bar'])

    def test_same_project_d_option_twice_configure(self):
        self._test_same_option_twice_configure(
            'one', ['-Done=foo', '-Done=bar'])

    def test_command_line(self):
        testdir = os.path.join(self.unit_test_dir, '30 command line')

        # Verify default values when passing no args
        self.init(testdir)
        obj = mesonbuild.coredata.load(self.builddir)
        self.assertEqual(obj.builtins['default_library'].value, 'static')
        self.assertEqual(obj.builtins['warning_level'].value, '1')
        self.assertEqual(obj.user_options['set_sub_opt'].value, True)
        self.assertEqual(obj.user_options['subp:subp_opt'].value, 'default3')
        self.wipe()

        # warning_level is special, it's --warnlevel instead of --warning-level
        # for historical reasons
        self.init(testdir, extra_args=['--warnlevel=2'])
        obj = mesonbuild.coredata.load(self.builddir)
        self.assertEqual(obj.builtins['warning_level'].value, '2')
        self.setconf('--warnlevel=3')
        obj = mesonbuild.coredata.load(self.builddir)
        self.assertEqual(obj.builtins['warning_level'].value, '3')
        self.wipe()

        # But when using -D syntax, it should be 'warning_level'
        self.init(testdir, extra_args=['-Dwarning_level=2'])
        obj = mesonbuild.coredata.load(self.builddir)
        self.assertEqual(obj.builtins['warning_level'].value, '2')
        self.setconf('-Dwarning_level=3')
        obj = mesonbuild.coredata.load(self.builddir)
        self.assertEqual(obj.builtins['warning_level'].value, '3')
        self.wipe()

        # Mixing --option and -Doption is forbidden
        with self.assertRaises(subprocess.CalledProcessError) as cm:
            self.init(testdir, extra_args=['--warnlevel=1', '-Dwarning_level=3'])
        self.assertNotEqual(0, cm.exception.returncode)
        self.assertIn('as both', cm.exception.output)
        self.init(testdir)
        with self.assertRaises(subprocess.CalledProcessError) as cm:
            self.setconf(['--warnlevel=1', '-Dwarning_level=3'])
        self.assertNotEqual(0, cm.exception.returncode)
        self.assertIn('as both', cm.exception.output)
        self.wipe()

        # --default-library should override default value from project()
        self.init(testdir, extra_args=['--default-library=both'])
        obj = mesonbuild.coredata.load(self.builddir)
        self.assertEqual(obj.builtins['default_library'].value, 'both')
        self.setconf('--default-library=shared')
        obj = mesonbuild.coredata.load(self.builddir)
        self.assertEqual(obj.builtins['default_library'].value, 'shared')
        if self.backend is Backend.ninja:
            # reconfigure target works only with ninja backend
            self.build('reconfigure')
            obj = mesonbuild.coredata.load(self.builddir)
            self.assertEqual(obj.builtins['default_library'].value, 'shared')
        self.wipe()

        # Should warn on unknown options
        out = self.init(testdir, extra_args=['-Dbad=1', '-Dfoo=2', '-Dwrong_link_args=foo'])
        self.assertIn('Unknown options: "bad, foo, wrong_link_args"', out)
        self.wipe()

        # Should fail on malformed option
        with self.assertRaises(subprocess.CalledProcessError) as cm:
            self.init(testdir, extra_args=['-Dfoo'])
        self.assertNotEqual(0, cm.exception.returncode)
        self.assertIn('Option \'foo\' must have a value separated by equals sign.', cm.exception.output)
        self.init(testdir)
        with self.assertRaises(subprocess.CalledProcessError) as cm:
            self.setconf('-Dfoo')
        self.assertNotEqual(0, cm.exception.returncode)
        self.assertIn('Option \'foo\' must have a value separated by equals sign.', cm.exception.output)
        self.wipe()

        # It is not an error to set wrong option for unknown subprojects or
        # language because we don't have control on which one will be selected.
        self.init(testdir, extra_args=['-Dc_wrong=1', '-Dwrong:bad=1', '-Db_wrong=1'])
        self.wipe()

        # Test we can set subproject option
        self.init(testdir, extra_args=['-Dsubp:subp_opt=foo'])
        obj = mesonbuild.coredata.load(self.builddir)
        self.assertEqual(obj.user_options['subp:subp_opt'].value, 'foo')
        self.wipe()

        # c_args value should be parsed with shlex
        self.init(testdir, extra_args=['-Dc_args=foo bar "one two"'])
        obj = mesonbuild.coredata.load(self.builddir)
        self.assertEqual(obj.compiler_options['c_args'].value, ['foo', 'bar', 'one two'])
        self.setconf('-Dc_args="foo bar" one two')
        obj = mesonbuild.coredata.load(self.builddir)
        self.assertEqual(obj.compiler_options['c_args'].value, ['foo bar', 'one', 'two'])
        self.wipe()

        # Setting a 2nd time the same option should override the first value
        try:
            self.init(testdir, extra_args=['--bindir=foo', '--bindir=bar',
                                           '-Dbuildtype=plain', '-Dbuildtype=release',
                                           '-Db_sanitize=address', '-Db_sanitize=thread',
                                           '-Dc_args=foo', '-Dc_args=bar'])
            obj = mesonbuild.coredata.load(self.builddir)
            self.assertEqual(obj.builtins['bindir'].value, 'bar')
            self.assertEqual(obj.builtins['buildtype'].value, 'release')
            self.assertEqual(obj.base_options['b_sanitize'].value, 'thread')
            self.assertEqual(obj.compiler_options['c_args'].value, ['bar'])
            self.setconf(['--bindir=bar', '--bindir=foo',
                          '-Dbuildtype=release', '-Dbuildtype=plain',
                          '-Db_sanitize=thread', '-Db_sanitize=address',
                          '-Dc_args=bar', '-Dc_args=foo'])
            obj = mesonbuild.coredata.load(self.builddir)
            self.assertEqual(obj.builtins['bindir'].value, 'foo')
            self.assertEqual(obj.builtins['buildtype'].value, 'plain')
            self.assertEqual(obj.base_options['b_sanitize'].value, 'address')
            self.assertEqual(obj.compiler_options['c_args'].value, ['foo'])
            self.wipe()
        except KeyError:
            # Ignore KeyError, it happens on CI for compilers that does not
            # support b_sanitize. We have to test with a base option because
            # they used to fail this test with Meson 0.46 an earlier versions.
            pass

    @unittest.skipIf(not os.path.isdir('docs'), 'Doc dir not found, presumably because this is a tarball release.')
    def test_compiler_options_documented(self):
        '''
        Test that C and C++ compiler options and base options are documented in
        Builtin-Options.md. Only tests the default compiler for the current
        platform on the CI.
        '''
        md = None
        with open('docs/markdown/Builtin-options.md') as f:
            md = f.read()
        self.assertIsNotNone(md)
        env = Environment('.', self.builddir, get_fake_options(self.prefix))
        # FIXME: Support other compilers
        cc = env.detect_c_compiler(False)
        cpp = env.detect_cpp_compiler(False)
        for comp in (cc, cpp):
            for opt in comp.get_options().keys():
                self.assertIn(opt, md)
            for opt in comp.base_options:
                self.assertIn(opt, md)
        self.assertNotIn('b_unknown', md)

    @unittest.skipIf(not os.path.isdir('docs'), 'Doc dir not found, presumably because this is a tarball release.')
    def test_cpu_families_documented(self):
        with open("docs/markdown/Reference-tables.md") as f:
            md = f.read()
        self.assertIsNotNone(md)

        sections = list(re.finditer(r"^## (.+)$", md, re.MULTILINE))
        for s1, s2 in zip(sections[::2], sections[1::2]):
            if s1.group(1) == "CPU families":
                # Extract the content for this section
                content = md[s1.end():s2.start()]
                # Find the list entries
                arches = [m.group(1) for m in re.finditer(r"^\| (\w+) +\|", content, re.MULTILINE)]
                # Drop the header
                arches = set(arches[1:])
                self.assertEqual(arches, set(mesonbuild.environment.known_cpu_families))

    def test_feature_check_usage_subprojects(self):
        testdir = os.path.join(self.unit_test_dir, '34 featurenew subprojects')
        out = self.init(testdir)
        # Parent project warns correctly
        self.assertRegex(out, "WARNING: Project targetting '>=0.45'.*'0.47.0': dict")
        # Subproject warns correctly
        self.assertRegex(out, "|WARNING: Project targetting '>=0.40'.*'0.44.0': disabler")
        # Subproject has a new-enough meson_version, no warning
        self.assertNotRegex(out, "WARNING: Project targetting.*Python")
        # Ensure a summary is printed in the subproject and the outer project
        self.assertRegex(out, "|WARNING: Project specifies a minimum meson_version '>=0.40'")
        self.assertRegex(out, "| * 0.44.0: {'disabler'}")
        self.assertRegex(out, "WARNING: Project specifies a minimum meson_version '>=0.45'")
        self.assertRegex(out, " * 0.47.0: {'dict'}")

    def test_configure_file_warnings(self):
        testdir = os.path.join(self.common_test_dir, "16 configure file")
        out = self.init(testdir)
        self.assertRegex(out, "WARNING:.*'empty'.*config.h.in.*not present.*")
        self.assertRegex(out, "WARNING:.*'FOO_BAR'.*nosubst-nocopy2.txt.in.*not present.*")
        self.assertRegex(out, "WARNING:.*'empty'.*config.h.in.*not present.*")
        self.assertRegex(out, "WARNING:.*empty configuration_data.*test.py.in")
        # No warnings about empty configuration data objects passed to files with substitutions
        self.assertNotRegex(out, "WARNING:.*empty configuration_data.*nosubst-nocopy1.txt.in")
        self.assertNotRegex(out, "WARNING:.*empty configuration_data.*nosubst-nocopy2.txt.in")

    def test_dirs(self):
        with tempfile.TemporaryDirectory() as containing:
            with tempfile.TemporaryDirectory(dir=containing) as srcdir:
                mfile = os.path.join(srcdir, 'meson.build')
                of = open(mfile, 'w')
                of.write("project('foobar', 'c')\n")
                of.close()
                pc = subprocess.run(self.setup_command,
                                    cwd=srcdir,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
                self.assertIn(b'Must specify at least one directory name', pc.stdout)
                with tempfile.TemporaryDirectory(dir=srcdir) as builddir:
                    subprocess.run(self.setup_command,
                                   check=True,
                                   cwd=builddir,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)

class FailureTests(BasePlatformTests):
    '''
    Tests that test failure conditions. Build files here should be dynamically
    generated and static tests should go into `test cases/failing*`.
    This is useful because there can be many ways in which a particular
    function can fail, and creating failing tests for all of them is tedious
    and slows down testing.
    '''
    dnf = "[Dd]ependency.*not found"

    def setUp(self):
        super().setUp()
        self.srcdir = os.path.realpath(tempfile.mkdtemp())
        self.mbuild = os.path.join(self.srcdir, 'meson.build')

    def tearDown(self):
        super().tearDown()
        windows_proof_rmtree(self.srcdir)

    def assertMesonRaises(self, contents, match, extra_args=None, langs=None):
        '''
        Assert that running meson configure on the specified @contents raises
        a error message matching regex @match.
        '''
        if langs is None:
            langs = []
        with open(self.mbuild, 'w') as f:
            f.write("project('failure test', 'c', 'cpp')\n")
            for lang in langs:
                f.write("add_languages('{}', required : false)\n".format(lang))
            f.write(contents)
        # Force tracebacks so we can detect them properly
        os.environ['MESON_FORCE_BACKTRACE'] = '1'
        with self.assertRaisesRegex(MesonException, match, msg=contents):
            # Must run in-process or we'll get a generic CalledProcessError
            self.init(self.srcdir, extra_args=extra_args, inprocess=True)

    def obtainMesonOutput(self, contents, match, extra_args, langs, meson_version):
        if langs is None:
            langs = []
        with open(self.mbuild, 'w') as f:
            core_version = '.'.join([str(component) for component in grab_leading_numbers(mesonbuild.coredata.version)])
            meson_version = meson_version or core_version
            f.write("project('output test', 'c', 'cpp', meson_version: '{}')\n".format(meson_version))
            for lang in langs:
                f.write("add_languages('{}', required : false)\n".format(lang))
            f.write(contents)
        # Run in-process for speed and consistency with assertMesonRaises
        return self.init(self.srcdir, extra_args=extra_args, inprocess=True)

    def assertMesonOutputs(self, contents, match, extra_args=None, langs=None, meson_version=None):
        '''
        Assert that running meson configure on the specified @contents outputs
        something that matches regex @match.
        '''
        out = self.obtainMesonOutput(contents, match, extra_args, langs, meson_version)
        self.assertRegex(out, match)

    def assertMesonDoesNotOutput(self, contents, match, extra_args=None, langs=None, meson_version=None):
        '''
        Assert that running meson configure on the specified @contents does not output
        something that matches regex @match.
        '''
        out = self.obtainMesonOutput(contents, match, extra_args, langs, meson_version)
        self.assertNotRegex(out, match)

    @skipIfNoPkgconfig
    def test_dependency(self):
        if subprocess.call(['pkg-config', '--exists', 'zlib']) != 0:
            raise unittest.SkipTest('zlib not found with pkg-config')
        a = (("dependency('zlib', method : 'fail')", "'fail' is invalid"),
             ("dependency('zlib', static : '1')", "[Ss]tatic.*boolean"),
             ("dependency('zlib', version : 1)", "[Vv]ersion.*string or list"),
             ("dependency('zlib', required : 1)", "[Rr]equired.*boolean"),
             ("dependency('zlib', method : 1)", "[Mm]ethod.*string"),
             ("dependency('zlibfail')", self.dnf),)
        for contents, match in a:
            self.assertMesonRaises(contents, match)

    def test_apple_frameworks_dependency(self):
        if not is_osx():
            raise unittest.SkipTest('only run on macOS')
        self.assertMesonRaises("dependency('appleframeworks')",
                               "requires at least one module")

    def test_sdl2_notfound_dependency(self):
        # Want to test failure, so skip if available
        if shutil.which('sdl2-config'):
            raise unittest.SkipTest('sdl2-config found')
        self.assertMesonRaises("dependency('sdl2', method : 'sdlconfig')", self.dnf)
        self.assertMesonRaises("dependency('sdl2', method : 'pkg-config')", self.dnf)

    def test_gnustep_notfound_dependency(self):
        # Want to test failure, so skip if available
        if shutil.which('gnustep-config'):
            raise unittest.SkipTest('gnustep-config found')
        self.assertMesonRaises("dependency('gnustep')",
                               "(requires a Objc compiler|{})".format(self.dnf),
                               langs = ['objc'])

    def test_wx_notfound_dependency(self):
        # Want to test failure, so skip if available
        if shutil.which('wx-config-3.0') or shutil.which('wx-config'):
            raise unittest.SkipTest('wx-config or wx-config-3.0 found')
        self.assertMesonRaises("dependency('wxwidgets')", self.dnf)
        self.assertMesonOutputs("dependency('wxwidgets', required : false)",
                                "Dependency .*WxWidgets.* found: .*NO.*")

    def test_wx_dependency(self):
        if not shutil.which('wx-config-3.0') and not shutil.which('wx-config'):
            raise unittest.SkipTest('Neither wx-config nor wx-config-3.0 found')
        self.assertMesonRaises("dependency('wxwidgets', modules : 1)",
                               "module argument is not a string")

    def test_llvm_dependency(self):
        self.assertMesonRaises("dependency('llvm', modules : 'fail')",
                               "(required.*fail|{})".format(self.dnf))

    def test_boost_notfound_dependency(self):
        # Can be run even if Boost is found or not
        self.assertMesonRaises("dependency('boost', modules : 1)",
                               "module.*not a string")
        self.assertMesonRaises("dependency('boost', modules : 'fail')",
                               "(fail.*not found|{})".format(self.dnf))

    def test_boost_BOOST_ROOT_dependency(self):
        # Test BOOST_ROOT; can be run even if Boost is found or not
        os.environ['BOOST_ROOT'] = 'relative/path'
        self.assertMesonRaises("dependency('boost')",
                               "(BOOST_ROOT.*absolute|{})".format(self.dnf))

    def test_dependency_invalid_method(self):
        code = '''zlib_dep = dependency('zlib', required : false)
        zlib_dep.get_configtool_variable('foo')
        '''
        self.assertMesonRaises(code, "'zlib' is not a config-tool dependency")
        code = '''zlib_dep = dependency('zlib', required : false)
        dep = declare_dependency(dependencies : zlib_dep)
        dep.get_pkgconfig_variable('foo')
        '''
        self.assertMesonRaises(code, "Method.*pkgconfig.*is invalid.*internal")
        code = '''zlib_dep = dependency('zlib', required : false)
        dep = declare_dependency(dependencies : zlib_dep)
        dep.get_configtool_variable('foo')
        '''
        self.assertMesonRaises(code, "Method.*configtool.*is invalid.*internal")

    def test_objc_cpp_detection(self):
        '''
        Test that when we can't detect objc or objcpp, we fail gracefully.
        '''
        env = Environment('', self.builddir, get_fake_options(self.prefix))
        try:
            env.detect_objc_compiler(False)
            env.detect_objcpp_compiler(False)
        except EnvironmentException:
            code = "add_languages('objc')\nadd_languages('objcpp')"
            self.assertMesonRaises(code, "Unknown compiler")
            return
        raise unittest.SkipTest("objc and objcpp found, can't test detection failure")

    def test_subproject_variables(self):
        '''
        Test that:
        1. The correct message is outputted when a not-required dep is not
           found and the fallback subproject is also not found.
        2. A not-found not-required dep with a fallback subproject outputs the
           correct message when the fallback subproject is found but the
           variable inside it is not.
        3. A fallback dependency is found from the subproject parsed in (2)
        4. A not-required fallback dependency is not found because the
           subproject failed to parse.
        '''
        tdir = os.path.join(self.unit_test_dir, '20 subproj dep variables')
        out = self.init(tdir, inprocess=True)
        self.assertRegex(out, r"Couldn't use fallback subproject "
                         "in.*subprojects.*nosubproj.*for the dependency.*somedep")
        self.assertRegex(out, r'Dependency.*somenotfounddep.*from subproject.*'
                         'subprojects.*somesubproj.*found:.*NO')
        self.assertRegex(out, r'Dependency.*zlibproxy.*from subproject.*'
                         'subprojects.*somesubproj.*found:.*YES.*(cached)')
        self.assertRegex(out, r'Couldn\'t use fallback subproject in '
                         '.*subprojects.*failingsubproj.*for the dependency.*somedep')

    def test_exception_exit_status(self):
        '''
        Test exit status on python exception
        '''
        tdir = os.path.join(self.unit_test_dir, '21 exit status')
        os.environ['MESON_UNIT_TEST'] = '1'
        with self.assertRaises(subprocess.CalledProcessError) as cm:
            self.init(tdir, inprocess=False)
        self.assertEqual(cm.exception.returncode, 2)
        self.wipe()

    def test_dict_requires_key_value_pairs(self):
        self.assertMesonRaises("dict = {3, 'foo': 'bar'}",
                               'Only key:value pairs are valid in dict construction.')
        self.assertMesonRaises("{'foo': 'bar', 3}",
                               'Only key:value pairs are valid in dict construction.')

    def test_dict_forbids_duplicate_keys(self):
        self.assertMesonRaises("dict = {'a': 41, 'a': 42}",
                               'Duplicate dictionary key: a.*')

    def test_dict_forbids_integer_key(self):
        self.assertMesonRaises("dict = {3: 'foo'}",
                               'Key must be a string.*')

    def test_using_too_recent_feature(self):
        # Here we use a dict, which was introduced in 0.47.0
        self.assertMesonOutputs("dict = {}",
                                ".*WARNING.*Project targetting.*but.*",
                                meson_version='>= 0.46.0')

    def test_using_recent_feature(self):
        # Same as above, except the meson version is now appropriate
        self.assertMesonDoesNotOutput("dict = {}",
                                      ".*WARNING.*Project targetting.*but.*",
                                      meson_version='>= 0.47.0')

    def test_using_too_recent_feature_dependency(self):
        self.assertMesonOutputs("dependency('pcap', required: false)",
                                ".*WARNING.*Project targetting.*but.*",
                                meson_version='>= 0.41.0')


class WindowsTests(BasePlatformTests):
    '''
    Tests that should run on Cygwin, MinGW, and MSVC
    '''
    def setUp(self):
        super().setUp()
        self.platform_test_dir = os.path.join(self.src_root, 'test cases/windows')

    @unittest.skipIf(is_cygwin(), 'Test only applicable to Windows')
    def test_find_program(self):
        '''
        Test that Windows-specific edge-cases in find_program are functioning
        correctly. Cannot be an ordinary test because it involves manipulating
        PATH to point to a directory with Python scripts.
        '''
        testdir = os.path.join(self.platform_test_dir, '9 find program')
        # Find `cmd` and `cmd.exe`
        prog1 = ExternalProgram('cmd')
        self.assertTrue(prog1.found(), msg='cmd not found')
        prog2 = ExternalProgram('cmd.exe')
        self.assertTrue(prog2.found(), msg='cmd.exe not found')
        self.assertPathEqual(prog1.get_path(), prog2.get_path())
        # Find cmd with an absolute path that's missing the extension
        cmd_path = prog2.get_path()[:-4]
        prog = ExternalProgram(cmd_path)
        self.assertTrue(prog.found(), msg='{!r} not found'.format(cmd_path))
        # Finding a script with no extension inside a directory works
        prog = ExternalProgram(os.path.join(testdir, 'test-script'))
        self.assertTrue(prog.found(), msg='test-script not found')
        # Finding a script with an extension inside a directory works
        prog = ExternalProgram(os.path.join(testdir, 'test-script-ext.py'))
        self.assertTrue(prog.found(), msg='test-script-ext.py not found')
        # Finding a script in PATH w/o extension works and adds the interpreter
        os.environ['PATH'] += os.pathsep + testdir
        prog = ExternalProgram('test-script-ext')
        self.assertTrue(prog.found(), msg='test-script-ext not found in PATH')
        self.assertPathEqual(prog.get_command()[0], python_command[0])
        self.assertPathBasenameEqual(prog.get_path(), 'test-script-ext.py')
        # Finding a script in PATH with extension works and adds the interpreter
        prog = ExternalProgram('test-script-ext.py')
        self.assertTrue(prog.found(), msg='test-script-ext.py not found in PATH')
        self.assertPathEqual(prog.get_command()[0], python_command[0])
        self.assertPathBasenameEqual(prog.get_path(), 'test-script-ext.py')

    def test_ignore_libs(self):
        '''
        Test that find_library on libs that are to be ignored returns an empty
        array of arguments. Must be a unit test because we cannot inspect
        ExternalLibraryHolder from build files.
        '''
        testdir = os.path.join(self.platform_test_dir, '1 basic')
        env = Environment(testdir, self.builddir, get_fake_options(self.prefix))
        cc = env.detect_c_compiler(False)
        if cc.id != 'msvc':
            raise unittest.SkipTest('Not using MSVC')
        # To force people to update this test, and also test
        self.assertEqual(set(cc.ignore_libs), {'c', 'm', 'pthread'})
        for l in cc.ignore_libs:
            self.assertEqual(cc.find_library(l, env, []), [])

    def test_rc_depends_files(self):
        testdir = os.path.join(self.platform_test_dir, '5 resources')

        # resource compiler depfile generation is not yet implemented for msvc
        env = Environment(testdir, self.builddir, get_fake_options(self.prefix))
        depfile_works = env.detect_c_compiler(False).get_id() != 'msvc'

        self.init(testdir)
        self.build()
        # Immediately rebuilding should not do anything
        self.assertBuildIsNoop()
        # Test compile_resources(depend_file:)
        # Changing mtime of sample.ico should rebuild prog
        self.utime(os.path.join(testdir, 'res', 'sample.ico'))
        self.assertRebuiltTarget('prog')
        # Test depfile generation by compile_resources
        # Changing mtime of resource.h should rebuild myres.rc and then prog
        if depfile_works:
            self.utime(os.path.join(testdir, 'inc', 'resource', 'resource.h'))
            self.assertRebuiltTarget('prog')
        self.wipe()

        if depfile_works:
            testdir = os.path.join(self.platform_test_dir, '13 resources with custom targets')
            self.init(testdir)
            self.build()
            # Immediately rebuilding should not do anything
            self.assertBuildIsNoop()
            # Changing mtime of resource.h should rebuild myres_1.rc and then prog_1
            self.utime(os.path.join(testdir, 'res', 'resource.h'))
            self.assertRebuiltTarget('prog_1')


class LinuxlikeTests(BasePlatformTests):
    '''
    Tests that should run on Linux and *BSD
    '''
    def test_basic_soname(self):
        '''
        Test that the soname is set correctly for shared libraries. This can't
        be an ordinary test case because we need to run `readelf` and actually
        check the soname.
        https://github.com/mesonbuild/meson/issues/785
        '''
        testdir = os.path.join(self.common_test_dir, '4 shared')
        self.init(testdir)
        self.build()
        lib1 = os.path.join(self.builddir, 'libmylib.so')
        soname = get_soname(lib1)
        self.assertEqual(soname, 'libmylib.so')

    def test_custom_soname(self):
        '''
        Test that the soname is set correctly for shared libraries when
        a custom prefix and/or suffix is used. This can't be an ordinary test
        case because we need to run `readelf` and actually check the soname.
        https://github.com/mesonbuild/meson/issues/785
        '''
        testdir = os.path.join(self.common_test_dir, '27 library versions')
        self.init(testdir)
        self.build()
        lib1 = os.path.join(self.builddir, 'prefixsomelib.suffix')
        soname = get_soname(lib1)
        self.assertEqual(soname, 'prefixsomelib.suffix')

    def test_pic(self):
        '''
        Test that -fPIC is correctly added to static libraries when b_staticpic
        is true and not when it is false. This can't be an ordinary test case
        because we need to inspect the compiler database.
        '''
        if is_cygwin() or is_osx():
            raise unittest.SkipTest('PIC not relevant')

        testdir = os.path.join(self.common_test_dir, '3 static')
        self.init(testdir)
        compdb = self.get_compdb()
        self.assertIn('-fPIC', compdb[0]['command'])
        self.setconf('-Db_staticpic=false')
        # Regenerate build
        self.build()
        compdb = self.get_compdb()
        self.assertNotIn('-fPIC', compdb[0]['command'])

    def test_pkgconfig_gen(self):
        '''
        Test that generated pkg-config files can be found and have the correct
        version and link args. This can't be an ordinary test case because we
        need to run pkg-config outside of a Meson build file.
        https://github.com/mesonbuild/meson/issues/889
        '''
        testdir = os.path.join(self.common_test_dir, '51 pkgconfig-gen')
        self.init(testdir)
        env = Environment(testdir, self.builddir, get_fake_options(self.prefix))
        kwargs = {'required': True, 'silent': True}
        os.environ['PKG_CONFIG_LIBDIR'] = self.privatedir
        foo_dep = PkgConfigDependency('libfoo', env, kwargs)
        self.assertTrue(foo_dep.found())
        self.assertEqual(foo_dep.get_version(), '1.0')
        self.assertIn('-lfoo', foo_dep.get_link_args())
        self.assertEqual(foo_dep.get_pkgconfig_variable('foo', {}), 'bar')
        self.assertPathEqual(foo_dep.get_pkgconfig_variable('datadir', {}), '/usr/data')

    def test_pkgconfig_gen_deps(self):
        '''
        Test that generated pkg-config files correctly handle dependencies
        '''
        testdir = os.path.join(self.common_test_dir, '51 pkgconfig-gen')
        self.init(testdir)
        privatedir1 = self.privatedir

        self.new_builddir()
        os.environ['PKG_CONFIG_LIBDIR'] = privatedir1
        testdir = os.path.join(self.common_test_dir, '51 pkgconfig-gen', 'dependencies')
        self.init(testdir)
        privatedir2 = self.privatedir

        os.environ['PKG_CONFIG_LIBDIR'] = os.pathsep.join([privatedir1, privatedir2])
        cmd = ['pkg-config', 'dependency-test']

        out = self._run(cmd + ['--print-requires']).strip().split('\n')
        self.assertEqual(sorted(out), sorted(['libexposed']))

        out = self._run(cmd + ['--print-requires-private']).strip().split('\n')
        self.assertEqual(sorted(out), sorted(['libfoo >= 1.0']))

        out = self._run(cmd + ['--cflags-only-other']).strip().split()
        self.assertEqual(sorted(out), sorted(['-pthread', '-DCUSTOM']))

        out = self._run(cmd + ['--libs-only-l', '--libs-only-other']).strip().split()
        self.assertEqual(sorted(out), sorted(['-pthread', '-lcustom',
                                              '-llibmain', '-llibexposed']))

        out = self._run(cmd + ['--libs-only-l', '--libs-only-other', '--static']).strip().split()
        self.assertEqual(sorted(out), sorted(['-pthread', '-lcustom',
                                              '-llibmain', '-llibexposed',
                                              '-llibinternal', '-lcustom2',
                                              '-lfoo']))

        cmd = ['pkg-config', 'requires-test']
        out = self._run(cmd + ['--print-requires']).strip().split('\n')
        self.assertEqual(sorted(out), sorted(['libexposed', 'libfoo >= 1.0', 'libhello']))

        cmd = ['pkg-config', 'requires-private-test']
        out = self._run(cmd + ['--print-requires-private']).strip().split('\n')
        self.assertEqual(sorted(out), sorted(['libexposed', 'libfoo >= 1.0', 'libhello']))

    def test_pkg_unfound(self):
        testdir = os.path.join(self.unit_test_dir, '22 unfound pkgconfig')
        self.init(testdir)
        with open(os.path.join(self.privatedir, 'somename.pc')) as f:
            pcfile = f.read()
        self.assertFalse('blub_blob_blib' in pcfile)

    def test_vala_c_warnings(self):
        '''
        Test that no warnings are emitted for C code generated by Vala. This
        can't be an ordinary test case because we need to inspect the compiler
        database.
        https://github.com/mesonbuild/meson/issues/864
        '''
        if not shutil.which('valac'):
            raise unittest.SkipTest('valac not installed.')
        testdir = os.path.join(self.vala_test_dir, '5 target glib')
        self.init(testdir)
        compdb = self.get_compdb()
        vala_command = None
        c_command = None
        for each in compdb:
            if each['file'].endswith('GLib.Thread.c'):
                vala_command = each['command']
            elif each['file'].endswith('GLib.Thread.vala'):
                continue
            elif each['file'].endswith('retcode.c'):
                c_command = each['command']
            else:
                m = 'Unknown file {!r} in vala_c_warnings test'.format(each['file'])
                raise AssertionError(m)
        self.assertIsNotNone(vala_command)
        self.assertIsNotNone(c_command)
        # -w suppresses all warnings, should be there in Vala but not in C
        self.assertIn(" -w ", vala_command)
        self.assertNotIn(" -w ", c_command)
        # -Wall enables all warnings, should be there in C but not in Vala
        self.assertNotIn(" -Wall ", vala_command)
        self.assertIn(" -Wall ", c_command)
        # -Werror converts warnings to errors, should always be there since it's
        # injected by an unrelated piece of code and the project has werror=true
        self.assertIn(" -Werror ", vala_command)
        self.assertIn(" -Werror ", c_command)

    @skipIfNoPkgconfig
    def test_qt5dependency_pkgconfig_detection(self):
        '''
        Test that qt4 and qt5 detection with pkgconfig works.
        '''
        # Verify Qt4 or Qt5 can be found with pkg-config
        qt4 = subprocess.call(['pkg-config', '--exists', 'QtCore'])
        qt5 = subprocess.call(['pkg-config', '--exists', 'Qt5Core'])
        if qt4 != 0 or qt5 != 0:
            raise unittest.SkipTest('Qt not found with pkg-config')
        testdir = os.path.join(self.framework_test_dir, '4 qt')
        self.init(testdir, ['-Dmethod=pkg-config'])
        # Confirm that the dependency was found with qmake
        msg = 'Qt4 native `pkg-config` dependency (modules: Core, Gui) found: YES\n'
        msg2 = 'Qt5 native `pkg-config` dependency (modules: Core, Gui) found: YES\n'
        mesonlog = self.get_meson_log()
        self.assertTrue(msg in mesonlog or msg2 in mesonlog)

    def test_qt5dependency_qmake_detection(self):
        '''
        Test that qt5 detection with qmake works. This can't be an ordinary
        test case because it involves setting the environment.
        '''
        # Verify that qmake is for Qt5
        if not shutil.which('qmake-qt5'):
            if not shutil.which('qmake'):
                raise unittest.SkipTest('QMake not found')
            output = subprocess.getoutput('qmake --version')
            if 'Qt version 5' not in output:
                raise unittest.SkipTest('Qmake found, but it is not for Qt 5.')
        # Disable pkg-config codepath and force searching with qmake/qmake-qt5
        testdir = os.path.join(self.framework_test_dir, '4 qt')
        self.init(testdir, ['-Dmethod=qmake'])
        # Confirm that the dependency was found with qmake
        msg = 'Qt5 native `qmake-qt5` dependency (modules: Core) found: YES\n'
        msg2 = 'Qt5 native `qmake` dependency (modules: Core) found: YES\n'
        mesonlog = self.get_meson_log()
        self.assertTrue(msg in mesonlog or msg2 in mesonlog)

    def _test_soname_impl(self, libpath, install):
        if is_cygwin() or is_osx():
            raise unittest.SkipTest('Test only applicable to ELF and linuxlike sonames')

        testdir = os.path.join(self.unit_test_dir, '1 soname')
        self.init(testdir)
        self.build()
        if install:
            self.install()

        # File without aliases set.
        nover = os.path.join(libpath, 'libnover.so')
        self.assertPathExists(nover)
        self.assertFalse(os.path.islink(nover))
        self.assertEqual(get_soname(nover), 'libnover.so')
        self.assertEqual(len(glob(nover[:-3] + '*')), 1)

        # File with version set
        verset = os.path.join(libpath, 'libverset.so')
        self.assertPathExists(verset + '.4.5.6')
        self.assertEqual(os.readlink(verset), 'libverset.so.4')
        self.assertEqual(get_soname(verset), 'libverset.so.4')
        self.assertEqual(len(glob(verset[:-3] + '*')), 3)

        # File with soversion set
        soverset = os.path.join(libpath, 'libsoverset.so')
        self.assertPathExists(soverset + '.1.2.3')
        self.assertEqual(os.readlink(soverset), 'libsoverset.so.1.2.3')
        self.assertEqual(get_soname(soverset), 'libsoverset.so.1.2.3')
        self.assertEqual(len(glob(soverset[:-3] + '*')), 2)

        # File with version and soversion set to same values
        settosame = os.path.join(libpath, 'libsettosame.so')
        self.assertPathExists(settosame + '.7.8.9')
        self.assertEqual(os.readlink(settosame), 'libsettosame.so.7.8.9')
        self.assertEqual(get_soname(settosame), 'libsettosame.so.7.8.9')
        self.assertEqual(len(glob(settosame[:-3] + '*')), 2)

        # File with version and soversion set to different values
        bothset = os.path.join(libpath, 'libbothset.so')
        self.assertPathExists(bothset + '.1.2.3')
        self.assertEqual(os.readlink(bothset), 'libbothset.so.1.2.3')
        self.assertEqual(os.readlink(bothset + '.1.2.3'), 'libbothset.so.4.5.6')
        self.assertEqual(get_soname(bothset), 'libbothset.so.1.2.3')
        self.assertEqual(len(glob(bothset[:-3] + '*')), 3)

    def test_soname(self):
        self._test_soname_impl(self.builddir, False)

    def test_installed_soname(self):
        libdir = self.installdir + os.path.join(self.prefix, self.libdir)
        self._test_soname_impl(libdir, True)

    def test_compiler_check_flags_order(self):
        '''
        Test that compiler check flags override all other flags. This can't be
        an ordinary test case because it needs the environment to be set.
        '''
        Oflag = '-O3'
        os.environ['CFLAGS'] = os.environ['CXXFLAGS'] = Oflag
        testdir = os.path.join(self.common_test_dir, '43 has function')
        self.init(testdir)
        cmds = self.get_meson_log_compiler_checks()
        for cmd in cmds:
            if cmd[0] == 'ccache':
                cmd = cmd[1:]
            # Verify that -I flags from the `args` kwarg are first
            # This is set in the '43 has function' test case
            self.assertEqual(cmd[1], '-I/tmp')
            # Verify that -O3 set via the environment is overridden by -O0
            Oargs = [arg for arg in cmd if arg.startswith('-O')]
            self.assertEqual(Oargs, [Oflag, '-O0'])

    def _test_stds_impl(self, testdir, compiler, p):
        lang_std = p + '_std'
        # Check that all the listed -std=xxx options for this compiler work
        # just fine when used
        for v in compiler.get_options()[lang_std].choices:
            if (compiler.get_id() == 'clang' and '17' in v and
                (version_compare(compiler.version, '<5.0.0') or
                 (compiler.clang_type == mesonbuild.compilers.CLANG_OSX and version_compare(compiler.version, '<9.2')))):
                continue
            std_opt = '{}={}'.format(lang_std, v)
            self.init(testdir, ['-D' + std_opt])
            cmd = self.get_compdb()[0]['command']
            if v != 'none':
                cmd_std = " -std={} ".format(v)
                self.assertIn(cmd_std, cmd)
            try:
                self.build()
            except:
                print('{} was {!r}'.format(lang_std, v))
                raise
            self.wipe()
        # Check that an invalid std option in CFLAGS/CPPFLAGS fails
        # Needed because by default ICC ignores invalid options
        cmd_std = '-std=FAIL'
        env_flags = p.upper() + 'FLAGS'
        os.environ[env_flags] = cmd_std
        self.init(testdir)
        cmd = self.get_compdb()[0]['command']
        qcmd_std = " {} ".format(cmd_std)
        self.assertIn(qcmd_std, cmd)
        with self.assertRaises(subprocess.CalledProcessError,
                               msg='{} should have failed'.format(qcmd_std)):
            self.build()

    def test_compiler_c_stds(self):
        '''
        Test that C stds specified for this compiler can all be used. Can't be
        an ordinary test because it requires passing options to meson.
        '''
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        env = Environment(testdir, self.builddir, get_fake_options(self.prefix))
        cc = env.detect_c_compiler(False)
        self._test_stds_impl(testdir, cc, 'c')

    def test_compiler_cpp_stds(self):
        '''
        Test that C++ stds specified for this compiler can all be used. Can't
        be an ordinary test because it requires passing options to meson.
        '''
        testdir = os.path.join(self.common_test_dir, '2 cpp')
        env = Environment(testdir, self.builddir, get_fake_options(self.prefix))
        cpp = env.detect_cpp_compiler(False)
        self._test_stds_impl(testdir, cpp, 'cpp')

    def test_unity_subproj(self):
        testdir = os.path.join(self.common_test_dir, '49 subproject')
        self.init(testdir, extra_args='--unity=subprojects')
        self.assertPathExists(os.path.join(self.builddir, 'subprojects/sublib/subprojects@sublib@@simpletest@exe/simpletest-unity.c'))
        self.assertPathExists(os.path.join(self.builddir, 'subprojects/sublib/subprojects@sublib@@sublib@sha/sublib-unity.c'))
        self.assertPathDoesNotExist(os.path.join(self.builddir, 'user@exe/user-unity.c'))
        self.build()

    def test_installed_modes(self):
        '''
        Test that files installed by these tests have the correct permissions.
        Can't be an ordinary test because our installed_files.txt is very basic.
        '''
        # Test file modes
        testdir = os.path.join(self.common_test_dir, '12 data')
        self.init(testdir)
        self.install()

        f = os.path.join(self.installdir, 'etc', 'etcfile.dat')
        found_mode = stat.filemode(os.stat(f).st_mode)
        want_mode = 'rw------T'
        self.assertEqual(want_mode, found_mode[1:])

        f = os.path.join(self.installdir, 'usr', 'bin', 'runscript.sh')
        statf = os.stat(f)
        found_mode = stat.filemode(statf.st_mode)
        want_mode = 'rwxr-sr-x'
        self.assertEqual(want_mode, found_mode[1:])
        if os.getuid() == 0:
            # The chown failed nonfatally if we're not root
            self.assertEqual(0, statf.st_uid)
            self.assertEqual(0, statf.st_gid)

        f = os.path.join(self.installdir, 'usr', 'share', 'progname',
                         'fileobject_datafile.dat')
        orig = os.path.join(testdir, 'fileobject_datafile.dat')
        statf = os.stat(f)
        statorig = os.stat(orig)
        found_mode = stat.filemode(statf.st_mode)
        orig_mode = stat.filemode(statorig.st_mode)
        self.assertEqual(orig_mode[1:], found_mode[1:])
        self.assertEqual(os.getuid(), statf.st_uid)
        if os.getuid() == 0:
            # The chown failed nonfatally if we're not root
            self.assertEqual(0, statf.st_gid)

        self.wipe()
        # Test directory modes
        testdir = os.path.join(self.common_test_dir, '66 install subdir')
        self.init(testdir)
        self.install()

        f = os.path.join(self.installdir, 'usr', 'share', 'sub1', 'second.dat')
        statf = os.stat(f)
        found_mode = stat.filemode(statf.st_mode)
        want_mode = 'rwxr-x--t'
        self.assertEqual(want_mode, found_mode[1:])
        if os.getuid() == 0:
            # The chown failed nonfatally if we're not root
            self.assertEqual(0, statf.st_uid)

    def test_installed_modes_extended(self):
        '''
        Test that files are installed with correct permissions using install_mode.
        '''
        testdir = os.path.join(self.common_test_dir, '201 install_mode')
        self.init(testdir)
        self.build()
        self.install()

        for fsobj, want_mode in [
                ('bin', 'drwxr-x---'),
                ('bin/runscript.sh', '-rwxr-sr-x'),
                ('bin/trivialprog', '-rwxr-sr-x'),
                ('include', 'drwxr-x---'),
                ('include/config.h', '-rw-rwSr--'),
                ('include/rootdir.h', '-r--r--r-T'),
                ('lib', 'drwxr-x---'),
                ('lib/libstat.a', '-rw---Sr--'),
                ('share', 'drwxr-x---'),
                ('share/man', 'drwxr-x---'),
                ('share/man/man1', 'drwxr-x---'),
                ('share/man/man1/foo.1.gz', '-r--r--r-T'),
                ('share/sub1', 'drwxr-x---'),
                ('share/sub1/second.dat', '-rwxr-x--t'),
                ('subdir', 'drwxr-x---'),
                ('subdir/data.dat', '-rw-rwSr--'),
        ]:
            f = os.path.join(self.installdir, 'usr', *fsobj.split('/'))
            found_mode = stat.filemode(os.stat(f).st_mode)
            self.assertEqual(want_mode, found_mode,
                             msg=('Expected file %s to have mode %s but found %s instead.' %
                                  (fsobj, want_mode, found_mode)))
        # Ensure that introspect --installed works on all types of files
        # FIXME: also verify the files list
        self.introspect('--installed')

    def test_install_umask(self):
        '''
        Test that files are installed with correct permissions using default
        install umask of 022, regardless of the umask at time the worktree
        was checked out or the build was executed.
        '''
        # Copy source tree to a temporary directory and change permissions
        # there to simulate a checkout with umask 002.
        orig_testdir = os.path.join(self.unit_test_dir, '24 install umask')
        # Create a new testdir under tmpdir.
        tmpdir = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(windows_proof_rmtree, tmpdir)
        testdir = os.path.join(tmpdir, '24 install umask')
        # Copy the tree using shutil.copyfile, which will use the current umask
        # instead of preserving permissions of the old tree.
        save_umask = os.umask(0o002)
        self.addCleanup(os.umask, save_umask)
        shutil.copytree(orig_testdir, testdir, copy_function=shutil.copyfile)
        # Preserve the executable status of subdir/sayhello though.
        os.chmod(os.path.join(testdir, 'subdir', 'sayhello'), 0o775)
        self.init(testdir)
        # Run the build under a 027 umask now.
        os.umask(0o027)
        self.build()
        # And keep umask 027 for the install step too.
        self.install()

        for executable in [
                'bin/prog',
                'share/subdir/sayhello',
        ]:
            f = os.path.join(self.installdir, 'usr', *executable.split('/'))
            found_mode = stat.filemode(os.stat(f).st_mode)
            want_mode = '-rwxr-xr-x'
            self.assertEqual(want_mode, found_mode,
                             msg=('Expected file %s to have mode %s but found %s instead.' %
                                  (executable, want_mode, found_mode)))

        for directory in [
                'usr',
                'usr/bin',
                'usr/include',
                'usr/share',
                'usr/share/man',
                'usr/share/man/man1',
                'usr/share/subdir',
        ]:
            f = os.path.join(self.installdir, *directory.split('/'))
            found_mode = stat.filemode(os.stat(f).st_mode)
            want_mode = 'drwxr-xr-x'
            self.assertEqual(want_mode, found_mode,
                             msg=('Expected directory %s to have mode %s but found %s instead.' %
                                  (directory, want_mode, found_mode)))

        for datafile in [
                'include/sample.h',
                'share/datafile.cat',
                'share/file.dat',
                'share/man/man1/prog.1.gz',
                'share/subdir/datafile.dog',
        ]:
            f = os.path.join(self.installdir, 'usr', *datafile.split('/'))
            found_mode = stat.filemode(os.stat(f).st_mode)
            want_mode = '-rw-r--r--'
            self.assertEqual(want_mode, found_mode,
                             msg=('Expected file %s to have mode %s but found %s instead.' %
                                  (datafile, want_mode, found_mode)))

    def test_cpp_std_override(self):
        testdir = os.path.join(self.unit_test_dir, '6 std override')
        self.init(testdir)
        compdb = self.get_compdb()
        for i in compdb:
            if 'prog03' in i['file']:
                c03_comp = i['command']
            if 'prog11' in i['file']:
                c11_comp = i['command']
            if 'progp' in i['file']:
                plain_comp = i['command']
        self.assertNotEqual(len(plain_comp), 0)
        self.assertIn('-std=c++03', c03_comp)
        self.assertNotIn('-std=c++11', c03_comp)
        self.assertIn('-std=c++11', c11_comp)
        self.assertNotIn('-std=c++03', c11_comp)
        self.assertNotIn('-std=c++03', plain_comp)
        self.assertNotIn('-std=c++11', plain_comp)
        # Now werror
        self.assertIn('-Werror', plain_comp)
        self.assertNotIn('-Werror', c03_comp)

    def test_run_installed(self):
        if is_cygwin() or is_osx():
            raise unittest.SkipTest('LD_LIBRARY_PATH and RPATH not applicable')

        testdir = os.path.join(self.unit_test_dir, '7 run installed')
        self.init(testdir)
        self.build()
        self.install()
        installed_exe = os.path.join(self.installdir, 'usr/bin/prog')
        installed_libdir = os.path.join(self.installdir, 'usr/foo')
        installed_lib = os.path.join(installed_libdir, 'libfoo.so')
        self.assertTrue(os.path.isfile(installed_exe))
        self.assertTrue(os.path.isdir(installed_libdir))
        self.assertTrue(os.path.isfile(installed_lib))
        # Must fail when run without LD_LIBRARY_PATH to ensure that
        # rpath has been properly stripped rather than pointing to the builddir.
        self.assertNotEqual(subprocess.call(installed_exe, stderr=subprocess.DEVNULL), 0)
        # When LD_LIBRARY_PATH is set it should start working.
        # For some reason setting LD_LIBRARY_PATH in os.environ fails
        # when all tests are run (but works when only this test is run),
        # but doing this explicitly works.
        env = os.environ.copy()
        env['LD_LIBRARY_PATH'] = installed_libdir
        self.assertEqual(subprocess.call(installed_exe, env=env), 0)
        # Ensure that introspect --installed works
        installed = self.introspect('--installed')
        for v in installed.values():
            self.assertTrue('prog' in v or 'foo' in v)

    def test_order_of_l_arguments(self):
        testdir = os.path.join(self.unit_test_dir, '9 -L -l order')
        os.environ['PKG_CONFIG_PATH'] = testdir
        self.init(testdir)
        # NOTE: .pc file has -Lfoo -lfoo -Lbar -lbar but pkg-config reorders
        # the flags before returning them to -Lfoo -Lbar -lfoo -lbar
        # but pkgconf seems to not do that. Sigh. Support both.
        expected_order = [('-L/me/first', '-lfoo1'),
                          ('-L/me/second', '-lfoo2'),
                          ('-L/me/first', '-L/me/second'),
                          ('-lfoo1', '-lfoo2'),
                          ('-L/me/second', '-L/me/third'),
                          ('-L/me/third', '-L/me/fourth',),
                          ('-L/me/third', '-lfoo3'),
                          ('-L/me/fourth', '-lfoo4'),
                          ('-lfoo3', '-lfoo4'),
                          ]
        with open(os.path.join(self.builddir, 'build.ninja')) as ifile:
            for line in ifile:
                if expected_order[0][0] in line:
                    for first, second in expected_order:
                        self.assertLess(line.index(first), line.index(second))
                    return
        raise RuntimeError('Linker entries not found in the Ninja file.')

    def test_introspect_dependencies(self):
        '''
        Tests that mesonintrospect --dependencies returns expected output.
        '''
        testdir = os.path.join(self.framework_test_dir, '7 gnome')
        self.init(testdir)
        glib_found = False
        gobject_found = False
        deps = self.introspect('--dependencies')
        self.assertIsInstance(deps, list)
        for dep in deps:
            self.assertIsInstance(dep, dict)
            self.assertIn('name', dep)
            self.assertIn('compile_args', dep)
            self.assertIn('link_args', dep)
            if dep['name'] == 'glib-2.0':
                glib_found = True
            elif dep['name'] == 'gobject-2.0':
                gobject_found = True
        self.assertTrue(glib_found)
        self.assertTrue(gobject_found)
        if subprocess.call(['pkg-config', '--exists', 'glib-2.0 >= 2.56.2']) != 0:
            raise unittest.SkipTest('glib >= 2.56.2 needed for the rest')
        targets = self.introspect('--targets')
        docbook_target = None
        for t in targets:
            if t['name'] == 'generated-gdbus-docbook':
                docbook_target = t
                break
        self.assertIsInstance(docbook_target, dict)
        ifile = self.introspect(['--target-files', 'generated-gdbus-docbook@cus'])[0]
        self.assertEqual(t['filename'], 'gdbus/generated-gdbus-doc-' + ifile)

    def test_build_rpath(self):
        if is_cygwin():
            raise unittest.SkipTest('Windows PE/COFF binaries do not use RPATH')
        testdir = os.path.join(self.unit_test_dir, '11 build_rpath')
        self.init(testdir)
        self.build()
        # C program RPATH
        build_rpath = get_rpath(os.path.join(self.builddir, 'prog'))
        self.assertEqual(build_rpath, '$ORIGIN/sub:/foo/bar')
        self.install()
        install_rpath = get_rpath(os.path.join(self.installdir, 'usr/bin/prog'))
        self.assertEqual(install_rpath, '/baz')
        # C++ program RPATH
        build_rpath = get_rpath(os.path.join(self.builddir, 'progcxx'))
        self.assertEqual(build_rpath, '$ORIGIN/sub:/foo/bar')
        self.install()
        install_rpath = get_rpath(os.path.join(self.installdir, 'usr/bin/progcxx'))
        self.assertEqual(install_rpath, 'baz')

    def test_pch_with_address_sanitizer(self):
        if is_cygwin():
            raise unittest.SkipTest('asan not available on Cygwin')

        testdir = os.path.join(self.common_test_dir, '13 pch')
        self.init(testdir, ['-Db_sanitize=address'])
        self.build()
        compdb = self.get_compdb()
        for i in compdb:
            self.assertIn("-fsanitize=address", i["command"])

    def test_coverage(self):
        gcovr_exe, gcovr_new_rootdir = mesonbuild.environment.detect_gcovr()
        if not gcovr_exe:
            raise unittest.SkipTest('gcovr not found')
        if not shutil.which('genhtml') and not gcovr_new_rootdir:
            raise unittest.SkipTest('genhtml not found and gcovr is too old')
        if 'clang' in os.environ.get('CC', ''):
            # We need to use llvm-cov instead of gcovr with clang
            raise unittest.SkipTest('Coverage does not work with clang right now, help wanted!')
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        self.init(testdir, ['-Db_coverage=true'])
        self.build()
        self.run_tests()
        self.run_target('coverage-html')

    def test_cross_find_program(self):
        testdir = os.path.join(self.unit_test_dir, '12 cross prog')
        crossfile = tempfile.NamedTemporaryFile(mode='w')
        print(os.path.join(testdir, 'some_cross_tool.py'))
        crossfile.write('''[binaries]
c = '/usr/bin/cc'
ar = '/usr/bin/ar'
strip = '/usr/bin/ar'
sometool.py = ['{0}']
someothertool.py = '{0}'

[properties]

[host_machine]
system = 'linux'
cpu_family = 'arm'
cpu = 'armv7' # Not sure if correct.
endian = 'little'
'''.format(os.path.join(testdir, 'some_cross_tool.py')))
        crossfile.flush()
        self.meson_cross_file = crossfile.name
        self.init(testdir)

    def test_reconfigure(self):
        testdir = os.path.join(self.unit_test_dir, '13 reconfigure')
        self.init(testdir, ['-Db_coverage=true'], default_args=False)
        self.build('reconfigure')

    def test_vala_generated_source_buildir_inside_source_tree(self):
        '''
        Test that valac outputs generated C files in the expected location when
        the builddir is a subdir of the source tree.
        '''
        if not shutil.which('valac'):
            raise unittest.SkipTest('valac not installed.')

        testdir = os.path.join(self.vala_test_dir, '8 generated sources')
        newdir = os.path.join(self.builddir, 'srctree')
        shutil.copytree(testdir, newdir)
        testdir = newdir
        # New builddir
        builddir = os.path.join(testdir, 'subdir/_build')
        os.makedirs(builddir, exist_ok=True)
        self.change_builddir(builddir)
        self.init(testdir)
        self.build()

    def test_old_gnome_module_codepaths(self):
        '''
        A lot of code in the GNOME module is conditional on the version of the
        glib tools that are installed, and breakages in the old code can slip
        by once the CI has a newer glib version. So we force the GNOME module
        to pretend that it's running on an ancient glib so the fallback code is
        also tested.
        '''
        testdir = os.path.join(self.framework_test_dir, '7 gnome')
        os.environ['MESON_UNIT_TEST_PRETEND_GLIB_OLD'] = "1"
        mesonbuild.modules.gnome.native_glib_version = '2.20'
        self.init(testdir, inprocess=True)
        self.build()
        mesonbuild.modules.gnome.native_glib_version = None

    @skipIfNoPkgconfig
    def test_pkgconfig_usage(self):
        testdir1 = os.path.join(self.unit_test_dir, '24 pkgconfig usage/dependency')
        testdir2 = os.path.join(self.unit_test_dir, '24 pkgconfig usage/dependee')
        if subprocess.call(['pkg-config', '--cflags', 'glib-2.0'],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) != 0:
            raise unittest.SkipTest('Glib 2.0 dependency not available.')
        with tempfile.TemporaryDirectory() as tempdirname:
            self.init(testdir1, ['--prefix=' + tempdirname, '--libdir=lib'], default_args=False)
            self.install(use_destdir=False)
            shutil.rmtree(self.builddir)
            os.mkdir(self.builddir)
            pkg_dir = os.path.join(tempdirname, 'lib/pkgconfig')
            self.assertTrue(os.path.exists(os.path.join(pkg_dir, 'libpkgdep.pc')))
            lib_dir = os.path.join(tempdirname, 'lib')
            os.environ['PKG_CONFIG_PATH'] = pkg_dir
            # Private internal libraries must not leak out.
            pkg_out = subprocess.check_output(['pkg-config', '--static', '--libs', 'libpkgdep'])
            self.assertFalse(b'libpkgdep-int' in pkg_out, 'Internal library leaked out.')
            # Dependencies must not leak to cflags when building only a shared library.
            pkg_out = subprocess.check_output(['pkg-config', '--cflags', 'libpkgdep'])
            self.assertFalse(b'glib' in pkg_out, 'Internal dependency leaked to headers.')
            # Test that the result is usable.
            self.init(testdir2)
            self.build()
            myenv = os.environ.copy()
            myenv['LD_LIBRARY_PATH'] = lib_dir
            if is_cygwin():
                bin_dir = os.path.join(tempdirname, 'bin')
                myenv['PATH'] = bin_dir + os.pathsep + myenv['PATH']
            self.assertTrue(os.path.isdir(lib_dir))
            test_exe = os.path.join(self.builddir, 'pkguser')
            self.assertTrue(os.path.isfile(test_exe))
            subprocess.check_call(test_exe, env=myenv)

    @skipIfNoPkgconfig
    def test_pkgconfig_internal_libraries(self):
        '''
        '''
        with tempfile.TemporaryDirectory() as tempdirname:
            # build library
            testdirbase = os.path.join(self.unit_test_dir, '28 pkgconfig use libraries')
            testdirlib = os.path.join(testdirbase, 'lib')
            self.init(testdirlib, extra_args=['--prefix=' + tempdirname,
                                              '--libdir=lib',
                                              '--default-library=static'], default_args=False)
            self.build()
            self.install(use_destdir=False)

            # build user of library
            pkg_dir = os.path.join(tempdirname, 'lib/pkgconfig')
            os.environ['PKG_CONFIG_PATH'] = pkg_dir
            self.new_builddir()
            self.init(os.path.join(testdirbase, 'app'))
            self.build()

    @skipIfNoPkgconfig
    def test_pkgconfig_formatting(self):
        testdir = os.path.join(self.unit_test_dir, '31 pkgconfig format')
        self.init(testdir)
        myenv = os.environ.copy()
        myenv['PKG_CONFIG_PATH'] = self.privatedir
        stdo = subprocess.check_output(['pkg-config', '--libs-only-l', 'libsomething'], env=myenv)
        deps = [b'-lgobject-2.0', b'-lgio-2.0', b'-lglib-2.0', b'-lsomething']
        if is_windows() or is_cygwin() or is_osx():
            # On Windows, libintl is a separate library
            deps.append(b'-lintl')
        self.assertEqual(set(deps), set(stdo.split()))

    def test_apple_bitcode(self):
        '''
        Test that -fembed-bitcode is correctly added while compiling and
        -bitcode_bundle is added while linking when b_bitcode is true and not
        when it is false.  This can't be an ordinary test case because we need
        to inspect the compiler database.
        '''
        if not is_osx():
            raise unittest.SkipTest('Apple bitcode only works on macOS')
        testdir = os.path.join(self.common_test_dir, '4 shared')
        # Try with bitcode enabled
        out = self.init(testdir, extra_args='-Db_bitcode=true')
        # Warning was printed
        self.assertRegex(out, 'WARNING:.*b_bitcode')
        # Compiler options were added
        compdb = self.get_compdb()
        self.assertIn('-fembed-bitcode', compdb[0]['command'])
        build_ninja = os.path.join(self.builddir, 'build.ninja')
        # Linker options were added
        with open(build_ninja, 'r', encoding='utf-8') as f:
            contents = f.read()
            m = re.search('LINK_ARGS =.*-bitcode_bundle', contents)
        self.assertIsNotNone(m, msg=contents)
        # Try with bitcode disabled
        self.setconf('-Db_bitcode=false')
        # Regenerate build
        self.build()
        compdb = self.get_compdb()
        self.assertNotIn('-fembed-bitcode', compdb[0]['command'])
        build_ninja = os.path.join(self.builddir, 'build.ninja')
        with open(build_ninja, 'r', encoding='utf-8') as f:
            contents = f.read()
            m = re.search('LINK_ARGS =.*-bitcode_bundle', contents)
        self.assertIsNone(m, msg=contents)

    def test_apple_bitcode_modules(self):
        '''
        Same as above, just for shared_module()
        '''
        if not is_osx():
            raise unittest.SkipTest('Apple bitcode not relevant')
        testdir = os.path.join(self.common_test_dir, '156 shared module resolving symbol in executable')
        # Ensure that it builds even with bitcode enabled
        self.init(testdir, extra_args='-Db_bitcode=true')
        self.build()
        self.run_tests()

    @skipIfNoPkgconfig
    def test_usage_external_library(self):
        '''
        Test that uninstalled usage of an external library (from the system or
        PkgConfigDependency) works. On macOS, this workflow works out of the
        box. On Linux, BSDs, Windows, etc, you need to set extra arguments such
        as LD_LIBRARY_PATH, etc, so this test is skipped.

        The system library is found with cc.find_library() and pkg-config deps.
        '''
        if not is_osx():
            raise unittest.SkipTest('workflow currently only works on macOS')
        oldprefix = self.prefix
        # Install external library so we can find it
        testdir = os.path.join(self.unit_test_dir, '33 external, internal library rpath', 'external library')
        # install into installdir without using DESTDIR
        installdir = self.installdir
        self.prefix = installdir
        self.init(testdir)
        self.prefix = oldprefix
        self.build()
        self.install(use_destdir=False)
        ## New builddir for the consumer
        self.new_builddir()
        os.environ['LIBRARY_PATH'] = os.path.join(installdir, self.libdir)
        os.environ['PKG_CONFIG_PATH'] = os.path.join(installdir, self.libdir, 'pkgconfig')
        testdir = os.path.join(self.unit_test_dir, '33 external, internal library rpath', 'built library')
        # install into installdir without using DESTDIR
        self.prefix = self.installdir
        self.init(testdir)
        self.prefix = oldprefix
        self.build()
        # test uninstalled
        self.run_tests()
        # test running after installation
        self.install(use_destdir=False)
        prog = os.path.join(self.installdir, 'bin', 'prog')
        self._run([prog])
        out = self._run(['otool', '-L', prog])
        self.assertNotIn('@rpath', out)
        ## New builddir for testing that DESTDIR is not added to install_name
        self.new_builddir()
        # install into installdir with DESTDIR
        self.init(testdir)
        self.build()
        # test running after installation
        self.install()
        prog = self.installdir + os.path.join(self.prefix, 'bin', 'prog')
        lib = self.installdir + os.path.join(self.prefix, 'lib', 'libbar_built.dylib')
        for f in prog, lib:
            out = self._run(['otool', '-L', f])
            # Ensure that the otool output does not contain self.installdir
            self.assertNotRegex(out, self.installdir + '.*dylib ')


class LinuxArmCrossCompileTests(BasePlatformTests):
    '''
    Tests that verify cross-compilation to Linux/ARM
    '''
    def setUp(self):
        super().setUp()
        src_root = os.path.dirname(__file__)
        self.meson_cross_file = os.path.join(src_root, 'cross', 'ubuntu-armhf.txt')

    def test_cflags_cross_environment_pollution(self):
        '''
        Test that the CFLAGS environment variable does not pollute the cross
        environment. This can't be an ordinary test case because we need to
        inspect the compiler database.
        '''
        testdir = os.path.join(self.common_test_dir, '3 static')
        os.environ['CFLAGS'] = '-DBUILD_ENVIRONMENT_ONLY'
        self.init(testdir)
        compdb = self.get_compdb()
        self.assertNotIn('-DBUILD_ENVIRONMENT_ONLY', compdb[0]['command'])

    def test_cross_file_overrides_always_args(self):
        '''
        Test that $lang_args in cross files always override get_always_args().
        Needed for overriding the default -D_FILE_OFFSET_BITS=64 on some
        architectures such as some Android versions and Raspbian.
        https://github.com/mesonbuild/meson/issues/3049
        https://github.com/mesonbuild/meson/issues/3089
        '''
        testdir = os.path.join(self.unit_test_dir, '29 cross file overrides always args')
        self.meson_cross_file = os.path.join(testdir, 'ubuntu-armhf-overrides.txt')
        self.init(testdir)
        compdb = self.get_compdb()
        self.assertRegex(compdb[0]['command'], '-D_FILE_OFFSET_BITS=64.*-U_FILE_OFFSET_BITS')
        self.build()


class PythonTests(BasePlatformTests):
    '''
    Tests that verify compilation of python extension modules
    '''
    def test_versions(self):
        if self.backend is not Backend.ninja:
            raise unittest.SkipTest('Skipping python tests with {} backend'.format(self.backend.name))

        testdir = os.path.join(self.src_root, 'test cases', 'unit', '32 python extmodule')

        # No python version specified, this will use meson's python
        self.init(testdir)
        self.build()
        self.run_tests()
        self.wipe()

        # When specifying a known name, (python2 / python3) the module
        # will also try 'python' as a fallback and use it if the major
        # version matches
        try:
            self.init(testdir, ['-Dpython=python2'])
            self.build()
            self.run_tests()
        except unittest.SkipTest:
            # python2 is not necessarily installed on the test machine,
            # if it is not, or the python headers can't be found, the test
            # will raise MESON_SKIP_TEST, we could check beforehand what version
            # of python is available, but it's a bit of a chicken and egg situation,
            # as that is the job of the module, so we just ask for forgiveness rather
            # than permission.
            pass

        self.wipe()

        for py in ('pypy', 'pypy3'):
            try:
                self.init(testdir, ['-Dpython=%s' % py])
            except unittest.SkipTest:
                # Same as above, pypy2 and pypy3 are not expected to be present
                # on the test system, the test project only raises in these cases
                continue

            # We have a pypy, this is expected to work
            self.build()
            self.run_tests()
            self.wipe()

        # The test is configured to error out with MESON_SKIP_TEST
        # in case it could not find python
        with self.assertRaises(unittest.SkipTest):
            self.init(testdir, ['-Dpython=not-python'])
        self.wipe()

        # While dir is an external command on both Windows and Linux,
        # it certainly isn't python
        with self.assertRaises(unittest.SkipTest):
            self.init(testdir, ['-Dpython=dir'])
        self.wipe()


class RewriterTests(unittest.TestCase):

    def setUp(self):
        super().setUp()
        src_root = os.path.dirname(__file__)
        self.testroot = os.path.realpath(tempfile.mkdtemp())
        self.rewrite_command = python_command + [os.path.join(src_root, 'mesonrewriter.py')]
        self.tmpdir = os.path.realpath(tempfile.mkdtemp())
        self.workdir = os.path.join(self.tmpdir, 'foo')
        self.test_dir = os.path.join(src_root, 'test cases/rewrite')

    def tearDown(self):
        windows_proof_rmtree(self.tmpdir)

    def read_contents(self, fname):
        with open(os.path.join(self.workdir, fname)) as f:
            return f.read()

    def check_effectively_same(self, mainfile, truth):
        mf = self.read_contents(mainfile)
        t = self.read_contents(truth)
        # Rewriting is not guaranteed to do a perfect job of
        # maintaining whitespace.
        self.assertEqual(mf.replace(' ', ''), t.replace(' ', ''))

    def prime(self, dirname):
        shutil.copytree(os.path.join(self.test_dir, dirname), self.workdir)

    def test_basic(self):
        self.prime('1 basic')
        subprocess.check_call(self.rewrite_command + ['remove',
                                                      '--target=trivialprog',
                                                      '--filename=notthere.c',
                                                      '--sourcedir', self.workdir],
                              universal_newlines=True)
        self.check_effectively_same('meson.build', 'removed.txt')
        subprocess.check_call(self.rewrite_command + ['add',
                                                      '--target=trivialprog',
                                                      '--filename=notthere.c',
                                                      '--sourcedir', self.workdir],
                              universal_newlines=True)
        self.check_effectively_same('meson.build', 'added.txt')
        subprocess.check_call(self.rewrite_command + ['remove',
                                                      '--target=trivialprog',
                                                      '--filename=notthere.c',
                                                      '--sourcedir', self.workdir],
                              universal_newlines=True)
        self.check_effectively_same('meson.build', 'removed.txt')

    def test_subdir(self):
        self.prime('2 subdirs')
        top = self.read_contents('meson.build')
        s2 = self.read_contents('sub2/meson.build')
        subprocess.check_call(self.rewrite_command + ['remove',
                                                      '--target=something',
                                                      '--filename=second.c',
                                                      '--sourcedir', self.workdir],
                              universal_newlines=True)
        self.check_effectively_same('sub1/meson.build', 'sub1/after.txt')
        self.assertEqual(top, self.read_contents('meson.build'))
        self.assertEqual(s2, self.read_contents('sub2/meson.build'))


def unset_envs():
    # For unit tests we must fully control all command lines
    # so that there are no unexpected changes coming from the
    # environment, for example when doing a package build.
    varnames = ['CPPFLAGS', 'LDFLAGS'] + list(mesonbuild.compilers.compilers.cflags_mapping.values())
    for v in varnames:
        if v in os.environ:
            del os.environ[v]

if __name__ == '__main__':
    unset_envs()
    cases = ['InternalTests', 'AllPlatformTests', 'FailureTests', 'PythonTests']
    if not is_windows():
        cases += ['LinuxlikeTests']
        if should_run_linux_cross_tests():
            cases += ['LinuxArmCrossCompileTests']
    if is_windows() or is_cygwin():
        cases += ['WindowsTests']

    unittest.main(defaultTest=cases, buffer=True)
