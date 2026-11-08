#include "hearing_intent.h"
#include <cassert>
#include <iostream>
using namespace collector::hearing;

static void owner_start_and_reconnect() {
    ListeningControl control;
    uint32_t token=99;
    assert(control.start_for_owner(1,true,token)==OwnerStart::Started);
    assert(token==2 && control.snapshot().requested);
    // The same operation used for transport reconnects must retain the owner.
    control.next_session();control.next_session();
    assert(control.generation()==3 && control.snapshot().revision==2);
    assert(control.stop_for_owner(token));
    assert(!control.snapshot().requested && control.snapshot().revision==3);
    assert(!control.stop_for_owner(token));
}

static void explicit_start_takes_over_even_when_already_listening() {
    ListeningControl control;
    uint32_t token=0;
    assert(control.start_for_owner(1,true,token)==OwnerStart::Started);
    assert(control.set_listening(true,true));
    assert(control.snapshot().revision==3);
    assert(!control.stop_for_owner(token));
    assert(control.snapshot().requested);
    token=99;
    assert(control.start_for_owner(3,true,token)==OwnerStart::AlreadyListening);
    assert(token==0 && control.snapshot().revision==3);
}

static void explicit_stop_invalidates_stale_recognition_and_release() {
    ListeningControl control;
    uint32_t token=0;
    assert(control.start_for_owner(1,true,token)==OwnerStart::Started);
    assert(control.set_listening(false,true));
    assert(!control.stop_for_owner(token));
    assert(!control.snapshot().requested && control.snapshot().revision==3);
    assert(control.start_for_owner(2,true,token)==OwnerStart::Superseded);
    assert(token==0 && !control.snapshot().requested);
    // Even a second idempotent stop is explicit intent for the coordinator.
    assert(control.set_listening(false,true));
    assert(control.snapshot().revision==4);
}

static void enrollment_takes_over_and_remains_protected() {
    ListeningControl control;
    uint32_t token=0,other=99;
    assert(control.start_for_owner(1,true,token)==OwnerStart::Started);
    control.begin_enrollment();
    assert(control.snapshot().revision==3 && control.snapshot().enrolling);
    assert(!control.stop_for_owner(token));
    assert(control.snapshot().requested && control.snapshot().enrolling);
    assert(control.start_for_owner(3,true,other)==OwnerStart::Unavailable);
    assert(other==0);
    control.finish_enrollment();
    assert(!control.snapshot().enrolling && control.snapshot().requested);
    assert(control.snapshot().revision==3 && !control.stop_for_owner(token));
}

static void explicit_cancel_and_config_invalidate_ownership() {
    ListeningControl control;
    uint32_t token=0;
    assert(control.start_for_owner(1,true,token)==OwnerStart::Started);
    control.cancel_enrollment(); // Explicit cancel revokes even without an enrollment.
    assert(control.snapshot().revision==3 && !control.stop_for_owner(token));
    assert(control.snapshot().requested);
    control.configure();
    assert(!control.snapshot().requested && !control.snapshot().enrolling);
    assert(control.snapshot().revision==4);
    assert(control.start_for_owner(3,true,token)==OwnerStart::Superseded);
    assert(control.start_for_owner(4,true,token)==OwnerStart::Started);
    control.configure();
    assert(!control.stop_for_owner(token) && !control.snapshot().requested);
}

static void unavailable_backend_cannot_acquire_ownership() {
    ListeningControl control;
    uint32_t token=99;
    assert(control.start_for_owner(1,false,token)==OwnerStart::Unavailable);
    assert(token==0 && !control.snapshot().requested && control.snapshot().revision==1);
    assert(!control.set_listening(true,false));
    assert(!control.snapshot().requested && control.snapshot().revision==2);
    assert(control.start_for_owner(1,true,token)==OwnerStart::Superseded);
    assert(control.generation()==1);
}

static void toggle_cancels_enrollment_and_does_not_use_connection_state() {
    ListeningControl control;
    control.begin_enrollment();
    assert(control.toggle_listening(true));
    assert(!control.snapshot().enrolling && !control.snapshot().requested);
    assert(control.snapshot().revision==3);
    assert(control.toggle_listening(true));
    assert(control.snapshot().requested && control.snapshot().revision==4);
    control.next_session();
    assert(control.toggle_listening(true));
    assert(!control.snapshot().requested && control.snapshot().revision==5);
}

int main() {
    owner_start_and_reconnect();
    explicit_start_takes_over_even_when_already_listening();
    explicit_stop_invalidates_stale_recognition_and_release();
    enrollment_takes_over_and_remains_protected();
    explicit_cancel_and_config_invalidate_ownership();
    unavailable_backend_cannot_acquire_ownership();
    toggle_cancels_enrollment_and_does_not_use_connection_state();
    std::cout<<"PASS hearing intent: owner/manual/enrollment/config/reconnect/unavailable/toggle\n";
}
