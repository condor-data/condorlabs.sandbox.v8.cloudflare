// Copyright (c) 2017-2025 Cloudflare, Inc.
// Licensed under the Apache 2.0 license found in the LICENSE file or at:
//     https://opensource.org/licenses/Apache-2.0

#pragma once

#include <workerd/io/io-context.h>
#include <workerd/jsg/async-context.h>

namespace workerd::api {

// JavaScript-accessible span class that manages span ownership through IoContext
class JsSpan: public jsg::Object {
 public:
  JsSpan(kj::Maybe<IoOwn<TraceContext>> span);
  ~JsSpan() noexcept(false);

  // Ends the span, marking its completion. Once ended, the span cannot be modified.
  // If the span is not explicitly ended, it will be automatically ended when the
  // JsSpan object is destroyed.
  void end();
  // Sets an attribute on the span. Values can be string, number, boolean, or undefined.
  // If undefined is passed, the attribute is not set (allows optional chaining).
  // Note: We intentionally don't support BigInt/int64_t. JavaScript numbers (doubles)
  // are sufficient for most tracing use cases, and BigInt conversion to int64_t would
  // require handling truncation for values outside the int64_t range.
  void setAttribute(
      jsg::Lock& js, kj::String key, jsg::Optional<kj::OneOf<bool, double, kj::String>> value);

  // Returns a SpanParent for this span, for use by TracingModule::enterContext() when
  // pushing it into the async context frame.  Returns a no-op nullptr parent if the
  // span has already ended.
  SpanParent getUserSpanParent();

  JSG_RESOURCE_TYPE(JsSpan) {
    JSG_METHOD(end);
    JSG_METHOD(setAttribute);
    JSG_DISPOSE(end);
  }

 private:
  kj::Maybe<IoOwn<TraceContext>> span;
};

// Disposable scope returned by TracingModule::enterContext(). Holds the two
// AsyncContextFrame::StorageScope objects that push the OTel Context JS object
// and the user-facing SpanParent into the async context frame for the duration
// of a startActiveSpan() / context.with() call.
//
// The TypeScript ContextManager calls [Symbol.dispose]() in its finally block to
// restore the previous frame, which propagates the pop across await automatically.
class ContextScope: public jsg::Object {
 public:
  // Constructs a ContextScope. The two scopes are pre-allocated on the heap and
  // ownership is transferred in.  The scopes MUST be destroyed in reverse order
  // (otelContextScope first, then userTraceScope) to restore the frame stack correctly.
  ContextScope(kj::Own<jsg::AsyncContextFrame::StorageScope> otelContextScope,
      kj::Own<jsg::AsyncContextFrame::StorageScope> userTraceScope);
  ~ContextScope() noexcept(false);

  // Called by [Symbol.dispose]() in TypeScript to explicitly restore the prior frame.
  void dispose(jsg::Lock& js);

  JSG_RESOURCE_TYPE(ContextScope) {
    JSG_METHOD(dispose);
    JSG_DISPOSE(dispose);
  }

 private:
  // Held via kj::Own so the scopes can live on the heap despite being non-movable.
  // kj::Own is nullable and movable even when T is not.
  // Destroyed in reverse declaration order: otelContextScope (inner) first,
  // then userTraceScope (outer), restoring the frame stack in LIFO order.
  kj::Own<jsg::AsyncContextFrame::StorageScope> otelContextScope;
  kj::Own<jsg::AsyncContextFrame::StorageScope> userTraceScope;
};

// Module that provides tracing capabilities for Workers.
// This module is available as "cloudflare-internal:tracing" and provides
// functionality to create and manage tracing spans.
class TracingModule: public jsg::Object {
 public:
  TracingModule() = default;
  TracingModule(jsg::Lock&, const jsg::Url&) {}

  // Creates a new tracing span with the given name.
  // The span will be associated with the current IoContext and will track
  // the execution of the code within its lifetime.
  // If no IoContext is available (e.g., during initialization), a no-op span
  // is returned that safely ignores all operations.
  //
  // Example usage:
  //   const span = tracing.startSpan("my-operation");
  //   try {
  //     // ... perform operation ...
  //   } finally {
  //     span.end();
  //   }
  jsg::Ref<JsSpan> startSpan(jsg::Lock& js, kj::String name);

  // Pushes the given OTel Context JS object and an optional native SpanParent into the
  // async context frame, returning a disposable ContextScope.  The TypeScript
  // ContextManager calls this from context.with() so that the new context propagates
  // across await automatically.
  //
  // - otelContextValue: the OTel Context JS object (opaque to C++; stored verbatim).
  // - nativeSpan: optional JsSpan whose underlying SpanParent becomes the new user
  //   trace parent. When null, the current user trace parent is preserved unchanged.
  jsg::Ref<ContextScope> enterContext(
      jsg::Lock& js, jsg::JsValue otelContextValue, jsg::Optional<jsg::Ref<JsSpan>> nativeSpan);

  // Returns the current OTel Context JS object stored in the async context frame, or
  // null/undefined if no context has been pushed (e.g., before the first enterContext call).
  jsg::Optional<jsg::JsValue> getCurrentOtelContext(jsg::Lock& js);

  JSG_RESOURCE_TYPE(TracingModule) {
    JSG_METHOD(startSpan);
    JSG_METHOD(enterContext);
    JSG_METHOD(getCurrentOtelContext);

    JSG_NESTED_TYPE(JsSpan);
    JSG_NESTED_TYPE(ContextScope);
  }
};

template <class Registry>
void registerTracingModule(Registry& registry, CompatibilityFlags::Reader flags) {
  registry.template addBuiltinModule<TracingModule>(
      "cloudflare-internal:tracing", workerd::jsg::ModuleRegistry::Type::INTERNAL);
}

template <typename TypeWrapper>
kj::Own<jsg::modules::ModuleBundle> getInternalTracingModuleBundle(auto featureFlags) {
  jsg::modules::ModuleBundle::BuiltinBuilder builder(
      jsg::modules::ModuleBundle::BuiltinBuilder::Type::BUILTIN_ONLY);
  static const auto kSpecifier = "cloudflare-internal:tracing"_url;
  builder.addObject<TracingModule, TypeWrapper>(kSpecifier);
  return builder.finish();
}
};  // namespace workerd::api

#define EW_TRACING_MODULE_ISOLATE_TYPES api::TracingModule, api::JsSpan, api::ContextScope
