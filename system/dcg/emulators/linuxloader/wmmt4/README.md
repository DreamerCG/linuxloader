# Wangan Midnight Maximum Tune 4 dependencies

`libcurl.so.4` is compiled from `curl-shim.c` with the Batocera i686
toolchain. WMN4r links a 32-bit libcurl (for Mucha, Namco's board
service), which Batocera does not carry; linuxloader answers the game's
servers itself (ALL.Net, Mucha and the game server, in-process), so the
library only has to load. It is a shim, not a curl. The generator adds
this directory only for WMMT4.

The rom is the cabinet's `/opt/arcade/exec` (WMN4r at its top, data/,
save/) with `lib/` for its own libraries: libprotobuf.so.7, OpenSSL 0.9.8
(libssl.so.0.9.8, libcrypto.so.0.9.8) and Cg 3.1 (libCg.so, libCgGL.so).
