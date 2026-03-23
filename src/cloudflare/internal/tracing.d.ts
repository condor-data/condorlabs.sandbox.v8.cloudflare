// Copyright (c) 2026 Cloudflare, Inc.
// Licensed under the Apache 2.0 license found in the LICENSE file or at:
//     https://opensource.org/licenses/Apache-2.0

export class Span {
  // Sets an attribute on this span. If value is undefined, the attribute is not set.
  setAttribute(key: string, value: string | number | boolean | undefined): void;
  // Closes the span
  end(): void;
}

// Disposable scope returned by enterContext(). Holds the two AsyncContextFrame
// storage scopes that propagate the OTel Context JS object and user-facing SpanParent
// across await boundaries. Call dispose() (or use Symbol.dispose) in a finally block.
export class ContextScope {
  dispose(): void;
  [Symbol.dispose](): void;
}

// Creates a new tracing span with the given name.
function startSpan(name: string): Span;

// Pushes the given OTel Context JS object and optional native Span into the async
// context frame, returning a disposable ContextScope.  The TypeScript ContextManager
// calls this from context.with() so the context propagates across await automatically.
//
// - otelContextValue: the OTel Context object (opaque to C++; stored and returned
//   verbatim from getCurrentOtelContext()).
// - nativeSpan: optional Span whose underlying SpanParent becomes the new user trace
//   parent.  When null/undefined, the current user trace parent is preserved.
function enterContext(
  otelContextValue: unknown,
  nativeSpan: Span | null | undefined
): ContextScope;

// Returns the current OTel Context JS object stored in the async context frame, or
// undefined if no context has been pushed yet.
function getCurrentOtelContext(): unknown | undefined;
