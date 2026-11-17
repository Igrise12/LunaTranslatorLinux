// LunaBridge: runs inside a game's Wine/Proton prefix and exposes the
// LunaHost{32,64}.dll C API to a native Linux LunaTranslator over TCP.
//
// The hook host talks to the injected LunaHook DLL through named pipes,
// shared memory and events, which are wineserver objects: only a process in
// the same prefix (same wineserver) can reach them. The Linux app therefore
// cannot load LunaHost itself; this helper does it and forwards every call
// and callback (1:1 with the Luna_* exports used by texthook.py).
//
// Wire format (little endian):
//   frame = u32 body_len | u16 type | u32 id | fields...
//   field = 'i' i64 | 'u' u64 | 'f' f64 | 's' u32 len utf8 | 'b' u32 len bytes
//         | 'T' u32 pid u64 addr u64 ctx u64 ctx2   (ThreadParam)
//
// Usage:
//   LunaBridge64.exe                     connect to the app (see endpoint())
//   LunaBridge32.exe --inject PID DLL    inject DLL into PID (bitness helper)
#include <ws2tcpip.h>
#include <tlhelp32.h>
#include <shellapi.h>
#include <condition_variable>
#include <cstdio>

#pragma comment(lib, "ws2_32.lib")

namespace proto
{
    constexpr int VERSION = 1;
    enum : uint16_t
    {
        HELLO = 1,
        REPLY = 2,
        LOG = 3,
        // app -> bridge
        START = 10,
        SETTINGS = 11,
        SETTINGS_EX = 12,
        RESET_LANG = 13,
        CONNECT = 14,
        NEED_INJECT = 15,
        DETACH = 16,
        INSERT_PC_HOOKS = 17,
        INSERT_HOOK = 18,
        REMOVE_HOOK = 19,
        FIND_HOOKS = 20,
        QUERY_HISTORY = 21,
        CHECK_USING_EMBED = 22,
        USE_EMBED = 23,
        EMBED_REPLY = 24,
        SYNC_THREAD = 25,
        LIST_PROCESSES = 30,
        INJECT = 31,
        WATCH_PROCESS = 32,
        LIST_MODULES = 33,
        TO_DOS = 34,
        TO_UNIX = 35,
        SHUTDOWN = 36,
        PROCESS_ALIVE = 37,
        // bridge -> app
        PROC_CONNECT = 50,
        PROC_DISCONNECT = 51,
        THREAD_CREATE = 52,
        THREAD_REMOVE = 53,
        OUTPUT = 54,
        HOST_INFO = 55,
        HOOK_INSERT = 56,
        EMBED_REQUEST = 57,
        I18N_QUERY = 58,
        EMU_GAME_INFO = 59,
        FIND_HOOK_RESULT = 60,
        PROCESS_EXIT = 61,
    };
}

// ---- UTF conversions ----------------------------------------------------

static std::string to_utf8(const wchar_t *s, size_t n)
{
    if (!s || !n)
        return {};
    int len = WideCharToMultiByte(CP_UTF8, 0, s, (int)n, nullptr, 0, nullptr, nullptr);
    std::string out(len, '\0');
    WideCharToMultiByte(CP_UTF8, 0, s, (int)n, out.data(), len, nullptr, nullptr);
    return out;
}
static std::string to_utf8(const std::wstring &s) { return to_utf8(s.c_str(), s.size()); }
static std::string to_utf8(const wchar_t *s) { return s ? to_utf8(s, wcslen(s)) : std::string(); }

static std::wstring from_utf8(const std::string &s)
{
    if (s.empty())
        return {};
    int len = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring out(len, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), out.data(), len);
    return out;
}

// ---- serialisation --------------------------------------------------------

struct Writer
{
    std::string body;
    Writer(uint16_t type, uint32_t id)
    {
        raw(&type, 2);
        raw(&id, 4);
    }
    void raw(const void *p, size_t n) { body.append((const char *)p, n); }
    Writer &i(int64_t v)
    {
        body.push_back('i');
        raw(&v, 8);
        return *this;
    }
    Writer &u(uint64_t v)
    {
        body.push_back('u');
        raw(&v, 8);
        return *this;
    }
    Writer &f(double v)
    {
        body.push_back('f');
        raw(&v, 8);
        return *this;
    }
    Writer &bytes(char tag, const std::string &s)
    {
        body.push_back(tag);
        uint32_t n = (uint32_t)s.size();
        raw(&n, 4);
        body.append(s);
        return *this;
    }
    Writer &s(const std::wstring &v) { return bytes('s', to_utf8(v)); }
    Writer &s(const wchar_t *v) { return bytes('s', to_utf8(v)); }
    Writer &b(const char *v) { return bytes('b', v ? std::string(v) : std::string()); }
    Writer &tp(const ThreadParam &t)
    {
        body.push_back('T');
        uint32_t pid = t.processId;
        raw(&pid, 4);
        raw(&t.addr, 8);
        raw(&t.ctx, 8);
        raw(&t.ctx2, 8);
        return *this;
    }
};

struct Reader
{
    const char *p, *e;
    uint16_t type = 0;
    uint32_t id = 0;
    Reader(const std::string &body) : p(body.data()), e(body.data() + body.size())
    {
        take(&type, 2);
        take(&id, 4);
    }
    void take(void *out, size_t n)
    {
        if ((size_t)(e - p) < n)
            throw std::runtime_error("short frame");
        memcpy(out, p, n);
        p += n;
    }
    char tag(char want)
    {
        char t;
        take(&t, 1);
        if (t != want)
            throw std::runtime_error("unexpected field type");
        return t;
    }
    int64_t i()
    {
        tag('i');
        int64_t v;
        take(&v, 8);
        return v;
    }
    uint64_t u()
    {
        tag('u');
        uint64_t v;
        take(&v, 8);
        return v;
    }
    double f()
    {
        tag('f');
        double v;
        take(&v, 8);
        return v;
    }
    std::string raw_bytes(char want)
    {
        tag(want);
        uint32_t n;
        take(&n, 4);
        if ((size_t)(e - p) < n)
            throw std::runtime_error("short string");
        std::string s(p, n);
        p += n;
        return s;
    }
    std::wstring s() { return from_utf8(raw_bytes('s')); }
    ThreadParam tp()
    {
        tag('T');
        ThreadParam t{};
        uint32_t pid;
        take(&pid, 4);
        t.processId = pid;
        take(&t.addr, 8);
        take(&t.ctx, 8);
        take(&t.ctx2, 8);
        return t;
    }
};

// ---- LunaHost API ---------------------------------------------------------

typedef void (*ProcessEvent)(DWORD pid);
typedef void (*ThreadEvent_maybe_embed)(const wchar_t *, const char *, ThreadParam, bool);
typedef void (*ThreadEvent)(const wchar_t *, const char *, ThreadParam);
typedef void (*OutputCallback)(const wchar_t *, const char *, ThreadParam, const wchar_t *);
typedef void (*HostInfoHandler)(HOSTINFO, const wchar_t *);
typedef void (*HookInsertHandler)(DWORD, uint64_t, const wchar_t *);
typedef void (*EmbedCallback)(const wchar_t *, ThreadParam);
typedef void (*EmuGameInfoCallback)(const wchar_t *, const wchar_t *, const wchar_t *);
typedef wchar_t *(*I18NQueryCallback)(const wchar_t *);
typedef void (*findhookcallback_t)(const wchar_t *, const wchar_t *);
typedef void (*historycallback_t)(const wchar_t *);

struct HostApi
{
    void (*Start)(ProcessEvent, ProcessEvent, ThreadEvent_maybe_embed, ThreadEvent, OutputCallback, HostInfoHandler, HookInsertHandler, EmbedCallback, I18NQueryCallback, EmuGameInfoCallback);
    bool (*CheckIfNeedInject)(DWORD);
    void (*ConnectProcess)(DWORD);
    void (*DetachProcess)(DWORD);
    void *(*AllocString)(const wchar_t *);
    void (*Settings)(int, int, int, int, bool);
    void (*SettingsEx)(DWORD, UINT32, UINT8, bool, wchar_t *, Displaymode, bool, bool, bool, float, bool, const wchar_t *);
    void (*ResetLang)();
    void (*InsertPCHooks)(DWORD, int);
    void (*InsertHookCode)(DWORD, LPCWSTR);
    void (*QueryThreadHistory)(ThreadParam, bool, historycallback_t);
    void (*RemoveHook)(DWORD, uint64_t);
    void (*FindHooks)(DWORD, SearchParam, findhookcallback_t, LPCWSTR);
    bool (*CheckIsUsingEmbed)(ThreadParam);
    void (*UseEmbed)(ThreadParam, bool);
    void (*EmbedCallback)(ThreadParam, LPCWSTR, LPCWSTR);
    void (*SyncThread)(ThreadParam, bool);
};

static std::wstring exe_dir()
{
    wchar_t path[MAX_PATH * 4];
    GetModuleFileNameW(nullptr, path, ARRAYSIZE(path));
    std::wstring s = path;
    return s.substr(0, s.find_last_of(L"\\/"));
}

template <class T>
static bool resolve(HMODULE m, const char *name, T &out)
{
    out = (T)GetProcAddress(m, name);
    return out != nullptr;
}

static bool load_host(HostApi &api, std::wstring &err)
{
    std::wstring dll = exe_dir() + (sizeof(void *) == 8 ? L"\\LunaHost64.dll" : L"\\LunaHost32.dll");
    HMODULE m = LoadLibraryW(dll.c_str());
    if (!m)
    {
        err = L"cannot load " + dll;
        return false;
    }
    bool ok = resolve(m, "Luna_Start", api.Start) && resolve(m, "Luna_CheckIfNeedInject", api.CheckIfNeedInject) && resolve(m, "Luna_ConnectProcess", api.ConnectProcess) && resolve(m, "Luna_DetachProcess", api.DetachProcess) && resolve(m, "Luna_AllocString", api.AllocString) && resolve(m, "Luna_Settings", api.Settings) && resolve(m, "Luna_SettingsEx", api.SettingsEx) && resolve(m, "Luna_ResetLang", api.ResetLang) && resolve(m, "Luna_InsertPCHooks", api.InsertPCHooks) && resolve(m, "Luna_InsertHookCode", api.InsertHookCode) && resolve(m, "Luna_QueryThreadHistory", api.QueryThreadHistory) && resolve(m, "Luna_RemoveHook", api.RemoveHook) && resolve(m, "Luna_FindHooks", api.FindHooks) && resolve(m, "Luna_CheckIsUsingEmbed", api.CheckIsUsingEmbed) && resolve(m, "Luna_UseEmbed", api.UseEmbed) && resolve(m, "Luna_EmbedCallback", api.EmbedCallback) && resolve(m, "Luna_SyncThread", api.SyncThread);
    if (!ok)
        err = L"LunaHost exports missing in " + dll;
    return ok;
}

// ---- Wine path helpers ------------------------------------------------------

typedef char *(CDECL *wine_get_unix_file_name_t)(LPCWSTR);
typedef WCHAR *(CDECL *wine_get_dos_file_name_t)(LPCSTR);

static std::string unix_path_of(const std::wstring &dos)
{
    static auto fn = (wine_get_unix_file_name_t)GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "wine_get_unix_file_name");
    if (!fn || dos.empty())
        return {};
    char *r = fn(dos.c_str());
    if (!r)
        return {};
    std::string s = r;
    HeapFree(GetProcessHeap(), 0, r);
    return s;
}

static std::wstring dos_path_of(const std::string &unixpath)
{
    static auto fn = (wine_get_dos_file_name_t)GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "wine_get_dos_file_name");
    if (!fn || unixpath.empty())
        return {};
    WCHAR *r = fn(unixpath.c_str());
    if (!r)
        return {};
    std::wstring s = r;
    HeapFree(GetProcessHeap(), 0, r);
    return s;
}

// ---- process helpers ----------------------------------------------------------

static bool process_is64(HANDLE h)
{
#ifdef _WIN64
    BOOL wow = FALSE;
    if (!IsWow64Process(h, &wow))
        return true;
    return !wow;
#else
    return false;
#endif
}

static std::wstring process_image(HANDLE h)
{
    wchar_t buf[MAX_PATH * 4];
    DWORD n = ARRAYSIZE(buf);
    if (QueryFullProcessImageNameW(h, 0, buf, &n))
        return std::wstring(buf, n);
    return {};
}

static int inject_same_bitness(DWORD pid, const std::wstring &dll)
{
    HANDLE proc = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION | PROCESS_VM_WRITE | PROCESS_VM_READ, FALSE, pid);
    if (!proc)
        return 0;
    size_t size = (dll.size() + 1) * sizeof(wchar_t);
    void *remote = VirtualAllocEx(proc, nullptr, size, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    int ok = 0;
    if (remote && WriteProcessMemory(proc, remote, dll.c_str(), size, nullptr))
    {
        auto loadlib = (LPTHREAD_START_ROUTINE)GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW");
        HANDLE th = CreateRemoteThread(proc, nullptr, 0, loadlib, remote, 0, nullptr);
        if (th)
        {
            WaitForSingleObject(th, 10000);
            DWORD code = 0;
            GetExitCodeThread(th, &code);
            ok = code != 0;
            CloseHandle(th);
        }
    }
    if (remote)
        VirtualFreeEx(proc, remote, 0, MEM_RELEASE);
    CloseHandle(proc);
    return ok;
}

static int inject(DWORD pid, const std::wstring &dll)
{
    HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!h)
        return 0;
    bool target64 = process_is64(h);
    CloseHandle(h);
    if (target64 == (sizeof(void *) == 8))
        return inject_same_bitness(pid, dll);
    // other bitness: delegate to the matching helper build
    std::wstring helper = exe_dir() + (target64 ? L"\\LunaBridge64.exe" : L"\\LunaBridge32.exe");
    std::wstring cmd = L"\"" + helper + L"\" --inject " + std::to_wstring(pid) + L" \"" + dll + L"\"";
    STARTUPINFOW si{sizeof(si)};
    PROCESS_INFORMATION pi{};
    if (!CreateProcessW(helper.c_str(), cmd.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi))
        return 0;
    WaitForSingleObject(pi.hProcess, 20000);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return code == 0;
}

// ---- the bridge ---------------------------------------------------------------

class Bridge
{
public:
    SOCKET sock = INVALID_SOCKET;
    HostApi api{};
    std::mutex send_mutex;
    std::atomic<bool> closing{false};

    // i18n requests wait for the app's REPLY
    std::mutex wait_mutex;
    std::condition_variable wait_cv;
    std::map<uint32_t, std::wstring> replies;
    std::set<uint32_t> waiting;
    std::atomic<uint32_t> next_id{0x80000000u};

    // command queue, executed in order on one worker thread
    std::mutex q_mutex;
    std::condition_variable q_cv;
    std::deque<std::string> queue;

    std::mutex pid_mutex;
    std::set<DWORD> connected;

    uint32_t find_request = 0;
    std::wstring history;

    bool send(const Writer &w)
    {
        uint32_t len = (uint32_t)w.body.size();
        std::lock_guard<std::mutex> lk(send_mutex);
        if (sock == INVALID_SOCKET)
            return false;
        std::string frame((const char *)&len, 4);
        frame += w.body;
        const char *p = frame.data();
        size_t left = frame.size();
        while (left)
        {
            int n = ::send(sock, p, (int)left, 0);
            if (n <= 0)
                return false;
            p += n;
            left -= n;
        }
        return true;
    }

    void log(const std::wstring &s) { send(Writer(proto::LOG, 0).s(s)); }

    bool recv_all(char *p, size_t n)
    {
        while (n)
        {
            int r = ::recv(sock, p, (int)n, 0);
            if (r <= 0)
                return false;
            p += r;
            n -= r;
        }
        return true;
    }

    bool recv_frame(std::string &body)
    {
        uint32_t len;
        if (!recv_all((char *)&len, 4))
            return false;
        if (len < 6 || len > (64u << 20))
            return false;
        body.resize(len);
        return recv_all(body.data(), len);
    }

    std::wstring ask_i18n(const wchar_t *text, bool &answered)
    {
        uint32_t id = next_id++;
        {
            std::lock_guard<std::mutex> lk(wait_mutex);
            waiting.insert(id);
        }
        send(Writer(proto::I18N_QUERY, id).s(text));
        std::unique_lock<std::mutex> lk(wait_mutex);
        answered = wait_cv.wait_for(lk, std::chrono::milliseconds(1000), [&]
                                    { return replies.count(id) > 0 || closing; });
        std::wstring r;
        if (replies.count(id))
        {
            r = replies[id];
            replies.erase(id);
        }
        else
            answered = false;
        waiting.erase(id);
        return r;
    }

    void reply(uint32_t id, Writer w) { send(w); }

    void handle(const std::string &body);
    void worker();
    void run();
};

static Bridge *g;

// LunaHost callbacks (plain function pointers, no context)
static void cb_connect(DWORD pid)
{
    {
        std::lock_guard<std::mutex> lk(g->pid_mutex);
        g->connected.insert(pid);
    }
    g->send(Writer(proto::PROC_CONNECT, 0).i(pid));
}
static void cb_disconnect(DWORD pid)
{
    {
        std::lock_guard<std::mutex> lk(g->pid_mutex);
        g->connected.erase(pid);
    }
    g->send(Writer(proto::PROC_DISCONNECT, 0).i(pid));
}
static void cb_create(const wchar_t *code, const char *name, ThreadParam tp, bool embed)
{
    g->send(Writer(proto::THREAD_CREATE, 0).s(code).b(name).tp(tp).i(embed));
}
static void cb_destroy(const wchar_t *code, const char *name, ThreadParam tp)
{
    g->send(Writer(proto::THREAD_REMOVE, 0).s(code).b(name).tp(tp));
}
static void cb_output(const wchar_t *code, const char *name, ThreadParam tp, const wchar_t *text)
{
    g->send(Writer(proto::OUTPUT, 0).s(code).b(name).tp(tp).s(text));
}
static void cb_hostinfo(HOSTINFO type, const wchar_t *text)
{
    g->send(Writer(proto::HOST_INFO, 0).i((int)type).s(text));
}
static void cb_hookinsert(DWORD pid, uint64_t addr, const wchar_t *code)
{
    g->send(Writer(proto::HOOK_INSERT, 0).i(pid).u(addr).s(code));
}
static void cb_embed(const wchar_t *text, ThreadParam tp)
{
    g->send(Writer(proto::EMBED_REQUEST, 0).s(text).tp(tp));
}
static wchar_t *cb_i18n(const wchar_t *text)
{
    bool answered = false;
    std::wstring r = g->ask_i18n(text, answered);
    if (!answered)
        r = text;
    // the host frees this with its own CRT: allocate through the DLL
    return (wchar_t *)g->api.AllocString(r.c_str());
}
static void cb_emuinfo(const wchar_t *id, const wchar_t *title, const wchar_t *version)
{
    g->send(Writer(proto::EMU_GAME_INFO, 0).s(id).s(title).s(version));
}
static void cb_findhook(const wchar_t *code, const wchar_t *text)
{
    g->send(Writer(proto::FIND_HOOK_RESULT, 0).i(g->find_request).s(code).s(text));
}
static void cb_history(const wchar_t *text)
{
    g->history = text ? text : L"";
}

static void watch_process(DWORD pid)
{
    std::thread([pid]
                {
        HANDLE h = OpenProcess(SYNCHRONIZE, FALSE, pid);
        if (h)
        {
            WaitForSingleObject(h, INFINITE);
            CloseHandle(h);
        }
        g->send(Writer(proto::PROCESS_EXIT, 0).i(pid)); })
        .detach();
}

void Bridge::handle(const std::string &body)
{
    Reader r(body);
    const uint32_t id = r.id;
    switch (r.type)
    {
    case proto::START:
        api.Start(cb_connect, cb_disconnect, cb_create, cb_destroy, cb_output, cb_hostinfo, cb_hookinsert, cb_embed, cb_i18n, cb_emuinfo);
        break;
    case proto::SETTINGS:
    {
        int a = (int)r.i(), b = (int)r.i(), c = (int)r.i(), d = (int)r.i();
        bool e = r.i() != 0;
        api.Settings(a, b, c, d, e);
        break;
    }
    case proto::SETTINGS_EX:
    {
        DWORD pid = (DWORD)r.i();
        UINT32 waittime = (UINT32)r.i();
        UINT8 charset = (UINT8)r.i();
        bool charsetEnabled = r.i() != 0;
        std::wstring family = r.s();
        Displaymode mode = (Displaymode)r.i();
        bool fastskip = r.i() != 0, clear = r.i() != 0, changesize = r.i() != 0;
        float rel = (float)r.f();
        bool veh = r.i() != 0;
        std::wstring unityfont = r.s();
        api.SettingsEx(pid, waittime, charset, charsetEnabled, family.data(), mode, fastskip, clear, changesize, rel, veh, unityfont.c_str());
        break;
    }
    case proto::RESET_LANG:
        api.ResetLang();
        break;
    case proto::CONNECT:
        api.ConnectProcess((DWORD)r.i());
        break;
    case proto::NEED_INJECT:
        reply(id, Writer(proto::REPLY, id).i(api.CheckIfNeedInject((DWORD)r.i())));
        break;
    case proto::DETACH:
        api.DetachProcess((DWORD)r.i());
        break;
    case proto::INSERT_PC_HOOKS:
    {
        DWORD pid = (DWORD)r.i();
        api.InsertPCHooks(pid, (int)r.i());
        break;
    }
    case proto::INSERT_HOOK:
    {
        DWORD pid = (DWORD)r.i();
        std::wstring code = r.s();
        api.InsertHookCode(pid, code.c_str());
        break;
    }
    case proto::REMOVE_HOOK:
    {
        DWORD pid = (DWORD)r.i();
        api.RemoveHook(pid, r.u());
        break;
    }
    case proto::FIND_HOOKS:
    {
        DWORD pid = (DWORD)r.i();
        // default-construct (member initializers); `sp{}` crashes MSVC 19.52
        SearchParam sp;
        std::string pattern = r.raw_bytes('b');
        memcpy(sp.pattern, pattern.data(), min(pattern.size(), sizeof(sp.pattern)));
        sp.address_method = (int)r.i();
        sp.search_method = (int)r.i();
        sp.length = (int)r.i();
        sp.offset = (int)r.i();
        sp.searchTime = (int)r.i();
        sp.maxRecords = (int)r.i();
        sp.codepage = (int)r.i();
        sp.padding = (intptr_t)r.i();
        sp.minAddress = (uintptr_t)r.u();
        sp.maxAddress = (uintptr_t)r.u();
        wcsncpy_s(sp.boundaryModule, r.s().c_str(), _TRUNCATE);
        wcsncpy_s(sp.exportModule, r.s().c_str(), _TRUNCATE);
        wcsncpy_s(sp.text, r.s().c_str(), _TRUNCATE);
        sp.isjithook = r.i() != 0;
        wcsncpy_s(sp.sharememname, r.s().c_str(), _TRUNCATE);
        sp.sharememsize = (size_t)r.u();
        std::wstring addresses = r.s();
        find_request = id;
        api.FindHooks(pid, sp, cb_findhook, addresses.empty() ? nullptr : addresses.c_str());
        break;
    }
    case proto::QUERY_HISTORY:
    {
        ThreadParam tp = r.tp();
        bool latest = r.i() != 0;
        history.clear();
        try
        {
            api.QueryThreadHistory(tp, latest, cb_history);
        }
        catch (...)
        {
        }
        reply(id, Writer(proto::REPLY, id).s(history));
        break;
    }
    case proto::CHECK_USING_EMBED:
        reply(id, Writer(proto::REPLY, id).i(api.CheckIsUsingEmbed(r.tp())));
        break;
    case proto::USE_EMBED:
    {
        ThreadParam tp = r.tp();
        api.UseEmbed(tp, r.i() != 0);
        break;
    }
    case proto::EMBED_REPLY:
    {
        ThreadParam tp = r.tp();
        std::wstring text = r.s(), trans = r.s();
        api.EmbedCallback(tp, text.c_str(), trans.c_str());
        break;
    }
    case proto::SYNC_THREAD:
    {
        ThreadParam tp = r.tp();
        api.SyncThread(tp, r.i() != 0);
        break;
    }
    case proto::LIST_PROCESSES:
    {
        Writer w(proto::REPLY, id);
        std::vector<Writer> rows;
        HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        PROCESSENTRY32W pe{sizeof(pe)};
        int count = 0;
        std::string payload;
        if (snap != INVALID_HANDLE_VALUE && Process32FirstW(snap, &pe))
        {
            do
            {
                HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pe.th32ProcessID);
                std::wstring image = h ? process_image(h) : L"";
                bool is64 = h ? process_is64(h) : false;
                if (h)
                    CloseHandle(h);
                Writer row(0, 0);
                row.i(pe.th32ProcessID).i(pe.th32ParentProcessID).s(pe.szExeFile).s(image).bytes('s', unix_path_of(image)).i(is64);
                payload += row.body.substr(6);
                count++;
            } while (Process32NextW(snap, &pe));
        }
        if (snap != INVALID_HANDLE_VALUE)
            CloseHandle(snap);
        w.i(count);
        w.body += payload;
        reply(id, w);
        break;
    }
    case proto::INJECT:
    {
        DWORD pid = (DWORD)r.i();
        std::wstring dll = r.s();
        reply(id, Writer(proto::REPLY, id).i(inject(pid, dll)));
        break;
    }
    case proto::WATCH_PROCESS:
        watch_process((DWORD)r.i());
        break;
    case proto::LIST_MODULES:
    {
        DWORD pid = (DWORD)r.i();
        std::vector<std::wstring> mods;
        HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
        MODULEENTRY32W me{sizeof(me)};
        if (snap != INVALID_HANDLE_VALUE && Module32FirstW(snap, &me))
        {
            do
                mods.push_back(me.szExePath);
            while (Module32NextW(snap, &me));
        }
        if (snap != INVALID_HANDLE_VALUE)
            CloseHandle(snap);
        Writer w(proto::REPLY, id);
        w.i((int64_t)mods.size());
        for (auto &m : mods)
            w.s(m);
        reply(id, w);
        break;
    }
    case proto::TO_DOS:
        reply(id, Writer(proto::REPLY, id).s(dos_path_of(r.raw_bytes('s'))));
        break;
    case proto::TO_UNIX:
        reply(id, Writer(proto::REPLY, id).bytes('s', unix_path_of(r.s())));
        break;
    case proto::PROCESS_ALIVE:
    {
        DWORD pid = (DWORD)r.i();
        HANDLE h = OpenProcess(SYNCHRONIZE, FALSE, pid);
        bool alive = h && WaitForSingleObject(h, 0) == WAIT_TIMEOUT;
        if (h)
            CloseHandle(h);
        reply(id, Writer(proto::REPLY, id).i(alive));
        break;
    }
    case proto::SHUTDOWN:
        closing = true;
        break;
    default:
        log(L"unknown command " + std::to_wstring(r.type));
    }
}

void Bridge::worker()
{
    while (true)
    {
        std::string body;
        {
            std::unique_lock<std::mutex> lk(q_mutex);
            q_cv.wait(lk, [&]
                      { return !queue.empty() || closing; });
            if (queue.empty())
                return;
            body = std::move(queue.front());
            queue.pop_front();
        }
        try
        {
            handle(body);
        }
        catch (std::exception &e)
        {
            log(L"bad command: " + from_utf8(e.what()));
        }
    }
}

void Bridge::run()
{
    std::thread work([this]
                     { worker(); });
    std::string body;
    while (!closing && recv_frame(body))
    {
        uint16_t type;
        uint32_t id;
        memcpy(&type, body.data(), 2);
        memcpy(&id, body.data() + 2, 4);
        if (type == proto::REPLY)
        {
            // answer to one of our I18N_QUERYs
            try
            {
                Reader r(body);
                std::wstring s = r.p < r.e ? r.s() : L"";
                std::lock_guard<std::mutex> lk(wait_mutex);
                if (waiting.count(id))
                    replies[id] = s;
            }
            catch (...)
            {
            }
            wait_cv.notify_all();
            continue;
        }
        if (type == proto::SHUTDOWN)
            break;
        {
            std::lock_guard<std::mutex> lk(q_mutex);
            queue.push_back(body);
        }
        q_cv.notify_one();
    }
    closing = true;
    q_cv.notify_all();
    wait_cv.notify_all();
    work.join();
    // the app is gone: release every game we were attached to
    std::set<DWORD> pids;
    {
        std::lock_guard<std::mutex> lk(pid_mutex);
        pids = connected;
    }
    for (DWORD pid : pids)
        api.DetachProcess(pid);
}

// ---- connection setup --------------------------------------------------------

static std::string env_utf8(const wchar_t *name)
{
    wchar_t buf[2048];
    DWORD n = GetEnvironmentVariableW(name, buf, ARRAYSIZE(buf));
    if (!n || n >= ARRAYSIZE(buf))
        return {};
    return to_utf8(buf, n);
}

// Endpoint and token: LUNA_BRIDGE_ENDPOINT ("127.0.0.1:port") and
// LUNA_BRIDGE_TOKEN, or the rendezvous file the app writes
// ($XDG_RUNTIME_DIR/lunatranslator/bridge.txt: "port\ntoken\n").
static bool endpoint(std::string &host, int &port, std::string &token)
{
    std::string ep = env_utf8(L"LUNA_BRIDGE_ENDPOINT");
    token = env_utf8(L"LUNA_BRIDGE_TOKEN");
    if (!ep.empty() && !token.empty())
    {
        auto colon = ep.rfind(':');
        host = ep.substr(0, colon);
        port = atoi(ep.c_str() + colon + 1);
        return port > 0;
    }
    std::string runtime = env_utf8(L"XDG_RUNTIME_DIR");
    if (runtime.empty())
        return false;
    std::wstring dos = dos_path_of(runtime + "/lunatranslator/bridge.txt");
    if (dos.empty())
        return false;
    FILE *f = _wfopen(dos.c_str(), L"rb");
    if (!f)
        return false;
    char line1[64] = {}, line2[256] = {};
    bool ok = fgets(line1, sizeof(line1), f) && fgets(line2, sizeof(line2), f);
    fclose(f);
    if (!ok)
        return false;
    host = "127.0.0.1";
    port = atoi(line1);
    token = line2;
    while (!token.empty() && (token.back() == '\n' || token.back() == '\r'))
        token.pop_back();
    return port > 0 && !token.empty();
}

static SOCKET connect_with_retry(int seconds)
{
    auto deadline = GetTickCount64() + (ULONGLONG)seconds * 1000;
    do
    {
        std::string host, token;
        int port = 0;
        if (endpoint(host, port, token))
        {
            SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
            sockaddr_in addr{};
            addr.sin_family = AF_INET;
            addr.sin_port = htons((u_short)port);
            inet_pton(AF_INET, host.c_str(), &addr.sin_addr);
            if (connect(s, (sockaddr *)&addr, sizeof(addr)) == 0)
            {
                int one = 1;
                setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char *)&one, sizeof(one));
                // first frame: HELLO with the token
                Writer hello(proto::HELLO, 0);
                std::string prefix = env_utf8(L"WINEPREFIX");
                hello.bytes('s', token).i(proto::VERSION).i(sizeof(void *) * 8).bytes('s', prefix).i(GetCurrentProcessId());
                uint32_t len = (uint32_t)hello.body.size();
                std::string frame((const char *)&len, 4);
                frame += hello.body;
                if (::send(s, frame.data(), (int)frame.size(), 0) == (int)frame.size())
                    return s;
            }
            closesocket(s);
        }
        Sleep(1000);
    } while (GetTickCount64() < deadline);
    return INVALID_SOCKET;
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, LPWSTR, int)
{
    int argc = 0;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argc >= 4 && wcscmp(argv[1], L"--inject") == 0)
        return inject((DWORD)_wtoi(argv[2]), argv[3]) ? 0 : 1;

    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);

    static Bridge bridge;
    g = &bridge;
    std::wstring err;
    bool host_ok = load_host(bridge.api, err);

    std::string retry = env_utf8(L"LUNA_BRIDGE_RETRY");
    bridge.sock = connect_with_retry(retry.empty() ? 300 : atoi(retry.c_str()));
    if (bridge.sock == INVALID_SOCKET)
        return 2;
    if (!host_ok)
    {
        bridge.log(err);
        return 3;
    }
    bridge.run();
    closesocket(bridge.sock);
    WSACleanup();
    // LunaHost keeps worker threads alive; end the process explicitly
    ExitProcess(0);
}
