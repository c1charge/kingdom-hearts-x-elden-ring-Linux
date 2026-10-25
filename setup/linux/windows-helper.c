/* MIT. Windows API bridge only; never contains game code or data.
 * x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -municode windows-helper.c -lversion -o kh-windows-helper.exe
 */
#define COBJMACROS
#include <windows.h>
#include <d3dcompiler.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

static int fail(const wchar_t *what) {
    fwprintf(stderr, L"%ls (Windows error %lu)\n", what, GetLastError());
    return 1;
}
static unsigned char *read_file(const wchar_t *file, size_t *size) {
    FILE *f = _wfopen(file, L"rb");
    if (!f) return NULL;
    if (fseek(f, 0, SEEK_END) || ftell(f) < 0) { fclose(f); return NULL; }
    *size = (size_t)ftell(f);
    rewind(f);
    unsigned char *data = malloc(*size ? *size : 1);
    if (!data || fread(data, 1, *size, f) != *size) { free(data); fclose(f); return NULL; }
    fclose(f);
    return data;
}
static int write_file(const wchar_t *file, const void *data, size_t size) {
    FILE *f = _wfopen(file, L"wb");
    if (!f) return fail(file);
    int ok = fwrite(data, 1, size, f) == size;
    if (fclose(f)) ok = 0;
    return ok ? 0 : fail(file);
}
static uint32_t be32(const unsigned char *b) {
    return ((uint32_t)b[0]<<24) | ((uint32_t)b[1]<<16) | ((uint32_t)b[2]<<8) | b[3];
}
static int disasm(const wchar_t *dir) {
    typedef HRESULT (WINAPI *Dis)(LPCVOID, SIZE_T, UINT, LPCSTR, ID3DBlob **);
    HMODULE module = LoadLibraryW(L"d3dcompiler_47.dll");
    Dis dis = module ? (Dis)(void *)GetProcAddress(module, "D3DDisassemble") : NULL;
    if (!dis) return fail(L"D3DDisassemble unavailable in d3dcompiler_47.dll");
    wchar_t pattern[32768], input[32768], output[32768];
    if (wcslen(dir) + 16 >= 32768) return fail(L"path too long");
    swprintf(pattern, 32768, L"%ls\\*.bin", dir);
    WIN32_FIND_DATAW entry;
    HANDLE search = FindFirstFileW(pattern, &entry);
    if (search == INVALID_HANDLE_VALUE) return fail(L"no DXBC input files");
    int result = 0;
    do {
        if (entry.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        if (wcslen(dir) + wcslen(entry.cFileName) + 2 >= 32768) { result = 1; break; }
        swprintf(input, 32768, L"%ls\\%ls", dir, entry.cFileName);
        wcscpy(output, input); wcscpy(output + wcslen(output) - 4, L".asm");
        if (GetFileAttributesW(output) != INVALID_FILE_ATTRIBUTES) continue;
        size_t size;
        unsigned char *data = read_file(input, &size);
        ID3DBlob *blob = NULL;
        HRESULT hr = data ? dis(data, size, 0, NULL, &blob) : E_FAIL;
        free(data);
        if (FAILED(hr) || !blob) { fwprintf(stderr, L"%ls: D3DDisassemble HRESULT %08lx\n", input, (unsigned long)hr); result = 1; break; }
        size_t n = ID3D10Blob_GetBufferSize(blob);
        const char *text = ID3D10Blob_GetBufferPointer(blob);
        if (n && text[n-1] == '\0') n--;
        result = write_file(output, text, n);
        ID3D10Blob_Release(blob);
        if (result) break;
    } while (FindNextFileW(search, &entry));
    FindClose(search);
    FreeLibrary(module);
    return result;
}
static int undcx(int argc, wchar_t **argv) {
    typedef int64_t (__cdecl *Decompress)(const void *, int64_t, void *, int64_t, int, int, int, void *, int64_t, void *, void *, void *, int64_t, int);
    HMODULE module = LoadLibraryW(argv[2]);
    Decompress decompress = module ? (Decompress)(void *)GetProcAddress(module, "OodleLZ_Decompress") : NULL;
    if (!decompress) return fail(L"OodleLZ_Decompress unavailable");
    for (int i = 3; i < argc; i++) {
        size_t size;
        unsigned char *data = read_file(argv[i], &size);
        if (!data || size < 0x4c || memcmp(data + 0x28, "KRAK", 4)) { free(data); return fail(L"not a KRAK DCX file"); }
        uint32_t usize = be32(data+0x1c), csize = be32(data+0x20);
        if (!usize || csize > size - 0x4c) { free(data); return fail(L"invalid DCX sizes"); }
        void *output = malloc(usize);
        if (!output) { free(data); return fail(L"DCX allocation failed"); }
        int64_t n = decompress(data+0x4c, csize, output, usize, 1, 0, 0, NULL, 0, NULL, NULL, NULL, 0, 3);
        free(data);
        if (n != usize) { free(output); return fail(L"Oodle returned an unexpected decompressed size"); }
        wchar_t *dest = _wcsdup(argv[i]);
        size_t len = wcslen(dest);
        if (len < 4 || wcscmp(dest + len - 4, L".dcx")) { free(dest); free(output); return fail(L"expected .dcx suffix"); }
        dest[len-4] = 0;
        int result = write_file(dest, output, usize);
        free(dest); free(output);
        if (result) return result;
    }
    FreeLibrary(module);
    return 0;
}
static int version(const wchar_t *file) {
    DWORD ignored, size = GetFileVersionInfoSizeW(file, &ignored);
    void *data = malloc(size ? size : 1);
    VS_FIXEDFILEINFO *info; UINT n;
    if (!data || !size || !GetFileVersionInfoW(file, 0, size, data) || !VerQueryValueW(data, L"\\", (void **)&info, &n)) { free(data); return fail(L"cannot read executable version"); }
    printf("%u.%u.%u.%u\n", HIWORD(info->dwFileVersionMS), LOWORD(info->dwFileVersionMS), HIWORD(info->dwFileVersionLS), LOWORD(info->dwFileVersionLS));
    free(data);
    return 0;
}
static int selfcheck(const wchar_t *mods) {
    typedef unsigned int (__cdecl *Check)(const wchar_t *, size_t);
    wchar_t dll[32768];
    if (wcslen(mods) + 16 >= 32768) return fail(L"path too long");
    swprintf(dll, 32768, L"%ls\\kh_er.dll", mods);
    HMODULE module = LoadLibraryW(dll);
    Check check = module ? (Check)(void *)GetProcAddress(module, "kh_er_selfcheck") : NULL;
    if (!check) return fail(L"kh_er_selfcheck unavailable");
    unsigned int n = check(mods, wcslen(mods));
    printf("kh_er_selfcheck: %u table group(s) read\n", n);
    FreeLibrary(module);
    return n ? 0 : 1;
}
int wmain(int argc, wchar_t **argv) {
    if (argc == 3 && !wcscmp(argv[1], L"disasm")) return disasm(argv[2]);
    if (argc >= 4 && !wcscmp(argv[1], L"undcx")) return undcx(argc, argv);
    if (argc == 3 && !wcscmp(argv[1], L"version")) return version(argv[2]);
    if (argc == 3 && !wcscmp(argv[1], L"selfcheck")) return selfcheck(argv[2]);
    fputs("usage: helper disasm DIR | undcx DLL FILE... | version EXE | selfcheck MODS\n", stderr);
    return 2;
}
