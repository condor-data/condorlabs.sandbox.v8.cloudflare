// Copyright (c) 2017-2025 Cloudflare, Inc.
// Licensed under the Apache 2.0 license found in the LICENSE file or at:
//     https://opensource.org/licenses/Apache-2.0

#include "tracing-module.h"

#include <workerd/jsg/promise.h>

namespace workerd::api {

// ---------------------------------------------------------------------------
// JsSpan

JsSpan::JsSpan(kj::Maybe<IoOwn<TraceContext>> span): span(kj::mv(span)) {}

JsSpan::~JsSpan() noexcept(false) {
  end();
}

void JsSpan::end() {
  span = kj::none;
}

void JsSpan::setAttribute(
    jsg::Lock& js, kj::String key, jsg::Optional<kj::OneOf<bool, double, kj::String>> maybeValue) {
  KJ_IF_SOME(s, span) {
    KJ_IF_SOME(value, maybeValue) {
      // JavaScript numbers (double) are stored as-is, not converted to int64_t
      s->setTag(kj::ConstString(kj::mv(key)), kj::mv(value));
    }
    // If value is undefined/none, we simply don't set the attribute
  }
}

SpanParent JsSpan::getUserSpanParent() {
  KJ_IF_SOME(s, span) {
    return s->getUserSpanParent();
  }
  // Span has already ended — return a no-op parent.
  return SpanParent(nullptr);
}

// ---------------------------------------------------------------------------
// ContextScope

ContextScope::ContextScope(kj::Own<jsg::AsyncContextFrame::StorageScope> otelContextScopeArg,
    kj::Own<jsg::AsyncContextFrame::StorageScope> userTraceScopeArg)
    : otelContextScope(kj::mv(otelContextScopeArg)),
      userTraceScope(kj::mv(userTraceScopeArg)) {}

ContextScope::~ContextScope() noexcept(false) {
  // kj::Own members are destroyed in reverse declaration order:
  //   1. userTraceScope (declared last) — outer scope, destroyed last would be wrong
  // Wait — declaration order is otelContextScope first, userTraceScope second.
  // C++ destroys members in reverse declaration order, so:
  //   1. userTraceScope (destroyed first — inner scope)
  //   2. otelContextScope (destroyed second — outer scope)
  // This is the correct LIFO order: inner frame first, then outer frame.
}

void ContextScope::dispose(jsg::Lock& js) {
  // Explicitly pop inner scope (user trace) first, then outer scope (OTel context)
  // to restore the async context frame stack in LIFO order.
  // Note: declaration order is otelContextScope, userTraceScope — so we clear
  // userTraceScope first (inner, pushed second) then otelContextScope (outer, pushed first).
  userTraceScope = nullptr;
  otelContextScope = nullptr;
}

// ---------------------------------------------------------------------------
// TracingModule

jsg::Ref<JsSpan> TracingModule::startSpan(jsg::Lock& js, kj::String name) {
  KJ_IF_SOME(ioContext, IoContext::tryCurrent()) {
    TraceContext traceContext = ioContext.makeUserTraceSpan(kj::ConstString(kj::mv(name)));
    auto ownedSpan = ioContext.addObject(kj::heap(kj::mv(traceContext)));
    return js.alloc<JsSpan>(kj::mv(ownedSpan));
  } else {
    // When no IoContext is available, create a no-op span
    return js.alloc<JsSpan>(kj::none);
  }
}

jsg::Ref<ContextScope> TracingModule::enterContext(
    jsg::Lock& js, jsg::JsValue otelContextValue, jsg::Optional<jsg::Ref<JsSpan>> nativeSpan) {
  auto& ioContext = IoContext::current();
  auto& workerLock = ioContext.getCurrentLock();

  // --- User trace scope (outer) ---
  // Push the native SpanParent from the given JsSpan (if any) into userTraceAsyncContextKey.
  // This allows makeUserTraceSpan() to pick up the correct parent for nested spans.
  SpanParent userSpanParent = [&]() -> SpanParent {
    KJ_IF_SOME(spanRef, nativeSpan) {
      return spanRef->getUserSpanParent();
    }
    // No span provided — preserve the current user trace parent unchanged.
    return ioContext.getCurrentUserTraceSpan();
  }();

  auto ioOwnUserSpan = ioContext.addObject(kj::heap(kj::mv(userSpanParent)));
  auto userSpanHandle = jsg::wrapOpaque(js.v8Context(), kj::mv(ioOwnUserSpan));
  // Allocate the user-trace scope on the heap (it's non-movable, but kj::Own is).
  // This is the outer scope — pushed first, restored last.
  auto userTraceStorageScope = kj::heap<jsg::AsyncContextFrame::StorageScope>(
      js, workerLock.getUserTraceAsyncContextKey(), js.v8Ref(userSpanHandle));

  // --- OTel context scope (inner) ---
  // Store the OTel Context JS object in otelContextAsyncContextKey.
  // The value is an arbitrary JS object; C++ treats it as opaque and returns it verbatim
  // from getCurrentOtelContext() when the TypeScript ContextManager.active() is called.
  // This is the inner scope — pushed second, restored first.
  auto otelContextStorageScope = kj::heap<jsg::AsyncContextFrame::StorageScope>(
      js, workerLock.getOtelContextAsyncContextKey(),
      js.v8Ref(static_cast<v8::Local<v8::Value>>(otelContextValue)));

  return js.alloc<ContextScope>(kj::mv(otelContextStorageScope), kj::mv(userTraceStorageScope));
}

jsg::Optional<jsg::JsValue> TracingModule::getCurrentOtelContext(jsg::Lock& js) {
  auto& ioContext = IoContext::current();
  auto& workerLock = ioContext.getCurrentLock();

  KJ_IF_SOME(frame, jsg::AsyncContextFrame::current(js)) {
    KJ_IF_SOME(value, frame.get(workerLock.getOtelContextAsyncContextKey())) {
      return jsg::JsValue(value.getHandle(js));
    }
  }
  return kj::none;
}

}  // namespace workerd::api
