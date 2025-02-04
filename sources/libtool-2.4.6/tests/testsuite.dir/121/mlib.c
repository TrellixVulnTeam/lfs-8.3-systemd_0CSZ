#include <config.h>
#include <stdio.h>

#include "foo.h"
#include "ltdl.h"

int test_dl (char *filename) {
  lt_dlhandle handle;
  const lt_dlinfo *info;
  int (*pfoo1)() = 0;
  int (*pfoo2)() = 0;
  int (*phello)() = 0;
  int *pnothing = 0;
  int ret = 0;

  handle = lt_dlopen(filename);
  if (!handle) {
    fprintf (stderr, "can't open the module %s!\n", filename);
    fprintf (stderr, "error was: %s\n", lt_dlerror());
    return 1;
  }

  info = lt_dlgetinfo(handle);
  if (!info) {
    fprintf (stderr, "can't get module info: %s\n", lt_dlerror());
    return 1;
  }
  if (info->name) {
    printf ("module name: %s\n", info->name);
  } else {
    printf ("module is not a libtool module\n");
  }
  printf ("module filename: %s\n", info->filename);
  printf ("module reference count: %i\n", info->ref_count);

  phello = (int(*)())lt_dlsym(handle, "hello");
  if (phello)
    {
      int value = (*phello) ();

      printf ("hello returned: %i\n", value);
      if (value == HELLO_RET)
        printf("hello is ok!\n");
    }
  else
    {
      fprintf (stderr, "did not find the 'hello' function\n");
      fprintf (stderr, "error was: %s\n", lt_dlerror());
      ret = 1;
    }

  pnothing = (int*)lt_dlsym(handle, "nothing");
  /* Try assigning to the nothing variable. */
  if (pnothing)
    *pnothing = 1;
  else
    {
      fprintf (stderr, "did not find the 'nothing' variable\n");
      fprintf (stderr, "error was: %s\n", lt_dlerror());
      ret = 1;
    }

  pfoo1 = (int(*)())lt_dlsym(handle, "foo1");
  /* Just call the functions and check return values. */
  if (pfoo1)
    {
      if ((*pfoo1) () == FOO_RET)
        printf("foo1 is ok!\n");
      else
	ret = 1;
    }
  else {
    pfoo2 = (int(*)())lt_dlsym(handle, "foo2");
    if (pfoo2)
      {
        if ((*pfoo2) () == FOO_RET)
          printf("foo2 is ok!\n");
        else ret = 1;
      }
    else
      {
        fprintf (stderr, "did not find any of the 'foo' functions\n");
        fprintf (stderr, "error was: %s\n", lt_dlerror());
        ret = 1;
      }
  }
  lt_dlclose(handle);
  return ret;
}

int
mlib_func (int argc, char **argv)
{
  int ret = 0;
  int i;
  /*
   * Would be nice if this somehow worked for libraries, not just executables.
   * LTDL_SET_PRELOADED_SYMBOLS();
   */
  if (lt_dlinit() != 0) {
    fprintf (stderr, "error during initialization: %s\n", lt_dlerror());
    return 1;
  }

  for (i = 1; i < argc; i++)
    if (test_dl(argv[i]))
       ret = 1;

  lt_dlexit();
  return ret;
}
