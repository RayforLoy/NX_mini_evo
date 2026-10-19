#include "nxe_runtime.h"

static int workspace_valid(const NxeWorkspace *w) {
    uintptr_t ptr[4]; uint32_t cap[4], i,j;
    if (!w) return 0;
    ptr[0]=(uintptr_t)w->package; cap[0]=w->package_capacity;
    ptr[1]=(uintptr_t)w->image; cap[1]=w->image_capacity;
    ptr[2]=(uintptr_t)w->state; cap[2]=w->state_capacity;
    ptr[3]=(uintptr_t)w->stack; cap[3]=w->stack_capacity;
    for (i=0;i<4;++i) {
        if (!ptr[i] || !cap[i] || ptr[i] > (uintptr_t)-1-cap[i]) return 0;
        for (j=0;j<i;++j)
            if (ptr[i]<ptr[j]+cap[j] && ptr[j]<ptr[i]+cap[i]) return 0;
    }
    return !(ptr[1]&7) && !(ptr[2]&3) && !(ptr[3]&7) && !(cap[3]&7);
}
static int invoke(NxeSession *s) {
    return nxe_call_on_stack(s->entry,&s->host,s->stack+s->info.stack_bytes);
}
void nxe_session_close(NxeSession *s) {
    if (s && s->active) {
        s->host.event=NXE_STOP;
        invoke(s);
        s->platform->release_display();
        s->active=0;
    }
}
int nxe_session_start(NxeSession *s,const NxePlatform *p,const NxeWorkspace *w,
                       const char *path,uint32_t seed) {
    NxeInfo info;
    uint32_t bytes=0, i;
    int result;
    if (!s || !p || p->abi!=NXE_ABI || p->struct_bytes<sizeof(NxePlatform) ||
        (p->capabilities & NXE_CAPABILITIES)!=NXE_CAPABILITIES ||
        !p->read_package || !p->claim_display || !p->present ||
        !p->release_display || !p->sync_code) return NXE_E_UNBOUND;
    if (s->active) return NXE_E_PLATFORM;
    if (!path || !workspace_valid(w) || w->package_capacity<NXE_HEADER_BYTES)
        return NXE_E_WORKSPACE;
    result=p->read_package(path,w->package,w->package_capacity,&bytes);
    if (result || bytes>w->package_capacity) return NXE_E_PLATFORM;
    result=nxe_validate(w->package,bytes,p->profile_sha256,&info);
    if (result) return result;
    if (w->state_capacity<info.state_bytes || w->stack_capacity<info.stack_bytes)
        return NXE_E_WORKSPACE;
    result=nxe_load(w->package,bytes,p->profile_sha256,w->image,w->image_capacity,&info);
    if (result) return result;
    if (p->sync_code(w->image,info.image_bytes+info.bss_bytes)) return NXE_E_PLATFORM;
    s->info=info; s->platform=p; s->stack=w->stack;
    s->entry=(NxeEntry)(uintptr_t)(w->image+info.entry_offset);
    for (i=0;i<info.state_bytes;++i) w->state[i]=0;
    s->host.abi=NXE_ABI; s->host.struct_bytes=sizeof(NxeHost);
    s->host.pixels=0; s->host.width=320; s->host.height=240; s->host.stride=320;
    s->host.buttons=0; s->host.pressed=0; s->host.elapsed_ms=0;
    s->host.state=w->state; s->host.state_bytes=info.state_bytes;
    s->host.seed=seed; s->host.event=NXE_INIT;
    if (p->claim_display(&s->host)) return NXE_E_PLATFORM;
    s->active=1;
    result=invoke(s);
    if (result || p->present(&s->host)) {
        nxe_session_close(s);
        return NXE_E_PLATFORM;
    }
    return NXE_OK;
}
int nxe_session_frame(NxeSession *s,uint32_t buttons,uint32_t elapsed_ms) {
    int result;
    if (!s || !s->active) return NXE_E_PLATFORM;
    s->host.pressed=buttons & ~s->host.buttons;
    s->host.buttons=buttons;
    s->host.elapsed_ms=elapsed_ms;
    s->host.event=NXE_FRAME;
    result=invoke(s);
    if (result!=NXE_CONTINUE) {
        nxe_session_close(s);
        return result;
    }
    if (s->platform->present(&s->host)) {
        nxe_session_close(s);
        return NXE_E_PLATFORM;
    }
    return NXE_CONTINUE;
}
