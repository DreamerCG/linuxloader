/* Wangan Midnight Maximum Tune 4 links libcurl.so.4 (32-bit, absent from
 * Batocera) for its Mucha board service. linuxloader answers those requests
 * itself (namcoEs1Wmmt4Net.c, before these functions are reached), so this
 * shim only has to load: the seven functions the game imports, transfers
 * failing as unreachable. Offered solely to WMMT4 by the generator.
 * Build: i686-buildroot-linux-gnu-gcc -O2 -shared -fPIC -Wl,-soname,libcurl.so.4 curl-shim.c -o libcurl.so.4
 */
#include <stdarg.h>
#include <stdlib.h>

#define CURLE_OK 0
#define CURLE_COULDNT_CONNECT 7
#define CURLINFO_LONG 0x200000

int curl_global_init(long flags)
{
    (void)flags;
    return CURLE_OK;
}

void curl_global_cleanup(void)
{
}

void *curl_easy_init(void)
{
    return calloc(1, 16);
}

int curl_easy_setopt(void *handle, int option, ...)
{
    (void)handle;
    (void)option;
    return CURLE_OK;
}

int curl_easy_perform(void *handle)
{
    (void)handle;
    return CURLE_COULDNT_CONNECT;
}

int curl_easy_getinfo(void *handle, int info, ...)
{
    va_list ap;
    (void)handle;
    va_start(ap, info);
    if ((info & 0xf00000) == CURLINFO_LONG)
    {
        long *value = va_arg(ap, long *);
        if (value)
            *value = 0;
    }
    va_end(ap);
    return CURLE_OK;
}

void curl_easy_cleanup(void *handle)
{
    free(handle);
}
