#!perl

#   !!!!!!!   DO NOT EDIT THIS FILE   !!!!!!!
#   This file is generated by write_buildcustomize.pl.
#   Any changes made here will be lost!

# We are miniperl, building extensions
# Replace the first entry of @INC ("lib") with the list of
# directories we need.
splice(@INC, 0, 1, q /mnt/lfs/sources/perl-5.28.0/cpan/AutoLoader/lib ,
        q /mnt/lfs/sources/perl-5.28.0/dist/Carp/lib ,
        q /mnt/lfs/sources/perl-5.28.0/dist/PathTools ,
        q /mnt/lfs/sources/perl-5.28.0/dist/PathTools/lib ,
        q /mnt/lfs/sources/perl-5.28.0/cpan/ExtUtils-Install/lib ,
        q /mnt/lfs/sources/perl-5.28.0/cpan/ExtUtils-MakeMaker/lib ,
        q /mnt/lfs/sources/perl-5.28.0/cpan/ExtUtils-Manifest/lib ,
        q /mnt/lfs/sources/perl-5.28.0/cpan/File-Path/lib ,
        q /mnt/lfs/sources/perl-5.28.0/ext/re ,
        q /mnt/lfs/sources/perl-5.28.0/dist/Term-ReadLine/lib ,
        q /mnt/lfs/sources/perl-5.28.0/dist/Exporter/lib ,
        q /mnt/lfs/sources/perl-5.28.0/ext/File-Find/lib ,
        q /mnt/lfs/sources/perl-5.28.0/cpan/Text-Tabs/lib ,
        q /mnt/lfs/sources/perl-5.28.0/dist/constant/lib ,
        q /mnt/lfs/sources/perl-5.28.0/cpan/version/lib ,
        q /mnt/lfs/sources/perl-5.28.0/lib );
$^O = 'linux';
__END__
