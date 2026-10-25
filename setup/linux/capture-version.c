/* MIT. Capture a Windows console program's --version directly to an inherited
 * file handle; Proton may not forward the original console's stdout to Linux.
 * No shell is involved, and the child argument is fixed to --version.
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
int wmain(int argc, wchar_t **argv) {
    if (argc != 3 || wcschr(argv[1], L'"') || wcslen(argv[1]) > 32000) {
        fputs("usage: capture-version EXE REPORT\n", stderr); return 2;
    }
    SECURITY_ATTRIBUTES security = {sizeof(security), NULL, TRUE};
    HANDLE output = CreateFileW(argv[2], GENERIC_WRITE, FILE_SHARE_READ, &security, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (output == INVALID_HANDLE_VALUE) return 3;
    HANDLE input = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &security, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (input == INVALID_HANDLE_VALUE) { CloseHandle(output); return 3; }
    wchar_t *command = calloc(wcslen(argv[1]) + 16, sizeof(wchar_t));
    if (!command) { CloseHandle(input); CloseHandle(output); return 3; }
    swprintf(command, wcslen(argv[1]) + 16, L"\"%ls\" --version", argv[1]);
    STARTUPINFOW startup = {0}; PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup); startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = input; startup.hStdOutput = output; startup.hStdError = output;
    BOOL created = CreateProcessW(argv[1], command, NULL, NULL, TRUE, CREATE_NO_WINDOW, NULL, NULL, &startup, &process);
    free(command); CloseHandle(input); CloseHandle(output);
    if (!created) return 4;
    DWORD code = 1;
    if (WaitForSingleObject(process.hProcess, 30000) != WAIT_OBJECT_0) {
        TerminateProcess(process.hProcess, 124);
        WaitForSingleObject(process.hProcess, 5000);
        code = 124;
    } else if (!GetExitCodeProcess(process.hProcess, &code)) code = 1;
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    return (int)code;
}
