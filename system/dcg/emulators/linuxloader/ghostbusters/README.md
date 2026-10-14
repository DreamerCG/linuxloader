# Ghostbusters Arcade dependencies

`libcsv.so` is the 32-bit library from the supplied TeknoParrot
`ElfLdr2/libs` directory, used in the confirmed native Ghostbusters v01.17
run. It provides the game's imported `csv_write2` function.

`libesd.so.0` is compiled from `unused-esd.c` with the Batocera i686
toolchain. Ghostbusters lists this library in `DT_NEEDED` but imports no
ESD functions. It is a dependency-only shim, not an ESD implementation.
The generator adds this directory only for Ghostbusters, leaving other
games to use their normal sound libraries.

The rom's `game`, `g6/`, `utils/` and `hasp/` can be kept directly at its
root. The generator also accepts the original `pm/game` layout and finds
additional native libraries in `lib/` beside the executable. Recorded
dongle answers remain with the rom in `hasp/`.
