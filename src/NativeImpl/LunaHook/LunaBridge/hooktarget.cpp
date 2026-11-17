// Test target for the Wine bridge: a window that redraws a changing
// Japanese line with GDI TextOutW every second, which LunaHook's PC hooks
// (GDI text APIs) pick up. Used by scripts/linux/bridge_smoke.py.
#include <windows.h>
#include <string>

static int counter = 0;

static LRESULT CALLBACK proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg)
    {
    case WM_TIMER:
        counter++;
        InvalidateRect(hwnd, nullptr, TRUE);
        return 0;
    case WM_PAINT:
    {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(hwnd, &ps);
        std::wstring line = L"テストの文章です " + std::to_wstring(counter); // テストの文章です N
        TextOutW(dc, 20, 20, line.c_str(), (int)line.size());
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, LPWSTR, int show)
{
    WNDCLASSW wc{};
    wc.lpfnWndProc = proc;
    wc.hInstance = inst;
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    wc.lpszClassName = L"LunaHookTarget";
    RegisterClassW(&wc);
    HWND hwnd = CreateWindowW(L"LunaHookTarget", L"LunaHook test target", WS_OVERLAPPEDWINDOW,
                              CW_USEDEFAULT, CW_USEDEFAULT, 480, 160, nullptr, nullptr, inst, nullptr);
    ShowWindow(hwnd, show);
    SetTimer(hwnd, 1, 1000, nullptr);
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0)
    {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return 0;
}
