// Copyright (c) 2026 Cloudflare, Inc.
// Licensed under the Apache 2.0 license found in the LICENSE file or at:
//     https://opensource.org/licenses/Apache-2.0

// Minimal OpenTelemetry API implementation that bridges to cloudflare-internal:tracing.
//
// This provides the TracerProvider, Tracer, Span, Context, and ContextManager
// implementations needed for `@opentelemetry/api` compatibility. The npm package
// discovers these providers via Symbol.for('opentelemetry.js.api.1'), which is
// populated by the otel-bootstrap.ts preamble.
//
// In this prototype, context propagation across `await` is NOT yet implemented.
// The ContextManager uses a simple stack. Phase 0a will add AsyncContextFrame
// integration for proper async propagation.

import tracing from 'cloudflare-internal:tracing';

// ---- Enums / Constants ----

export const SpanStatusCode = { UNSET: 0, OK: 1, ERROR: 2 } as const;
export const SpanKind = {
  INTERNAL: 0,
  SERVER: 1,
  CLIENT: 2,
  PRODUCER: 3,
  CONSUMER: 4,
} as const;
const TraceFlags = { NONE: 0x0, SAMPLED: 0x1 } as const;

const INVALID_SPANID = '0000000000000000';
const INVALID_TRACEID = '00000000000000000000000000000000';
const INVALID_SPAN_CONTEXT: SpanContext = {
  traceId: INVALID_TRACEID,
  spanId: INVALID_SPANID,
  traceFlags: TraceFlags.NONE,
};

// ---- Types ----

interface SpanContext {
  traceId: string;
  spanId: string;
  traceFlags: number;
  traceState?: unknown;
  isRemote?: boolean;
}

interface SpanStatus {
  code: number;
  message?: string;
}

// ---- Context (immutable key-value bag) ----

const SPAN_KEY = Symbol.for('OpenTelemetry Context Key SPAN');

type ContextEntries = ReadonlyMap<symbol, unknown>;

class OtelContext {
  readonly #entries: ContextEntries;

  constructor(entries?: ContextEntries) {
    this.#entries = entries ?? new Map();
  }

  getValue(key: symbol): unknown {
    return this.#entries.get(key);
  }

  setValue(key: symbol, value: unknown): OtelContext {
    const copy = new Map(this.#entries);
    copy.set(key, value);
    return new OtelContext(copy);
  }

  deleteValue(key: symbol): OtelContext {
    const copy = new Map(this.#entries);
    copy.delete(key);
    return new OtelContext(copy);
  }
}

const ROOT_CONTEXT = new OtelContext();

export function getSpanFromContext(ctx: OtelContext): WorkerdSpan | undefined {
  return ctx.getValue(SPAN_KEY) as WorkerdSpan | undefined;
}

export function setSpanInContext(
  ctx: OtelContext,
  span: WorkerdSpan
): OtelContext {
  return ctx.setValue(SPAN_KEY, span);
}

// ---- Context Manager ----
//
// For the prototype, this uses a simple module-level variable to track the
// "current" context. This does NOT propagate across await — that requires
// Phase 0a's AsyncContextFrame integration. But it's enough to demonstrate
// the Symbol.for() bootstrap and span creation.

let currentContext: OtelContext = ROOT_CONTEXT;

interface ContextManager {
  active(): OtelContext;
  with<A extends unknown[], F extends (...args: A) => ReturnType<F>>(
    ctx: OtelContext,
    fn: F,
    thisArg?: ThisParameterType<F>,
    ...args: A
  ): ReturnType<F>;
  bind<T>(ctx: OtelContext, target: (...args: unknown[]) => T): typeof target;
  enable(): ContextManager;
  disable(): ContextManager;
}

const contextManager: ContextManager = {
  active(): OtelContext {
    return currentContext;
  },

  with<A extends unknown[], F extends (...args: A) => ReturnType<F>>(
    ctx: OtelContext,
    fn: F,
    thisArg?: ThisParameterType<F>,
    ...args: A
  ): ReturnType<F> {
    const previous = currentContext;
    currentContext = ctx;
    try {
      return fn.apply(thisArg, args);
    } finally {
      currentContext = previous;
    }
  },

  bind<T>(ctx: OtelContext, target: (...args: unknown[]) => T): typeof target {
    return (...args: unknown[]) =>
      contextManager.with(ctx, target, undefined, ...args);
  },

  enable(): ContextManager {
    return contextManager;
  },

  disable(): ContextManager {
    return contextManager;
  },
};

// ---- Non-recording span (sentinel for invalid/no-op contexts) ----

class NonRecordingSpan {
  readonly #spanContext: SpanContext;

  constructor(spanContext?: SpanContext) {
    this.#spanContext = spanContext ?? INVALID_SPAN_CONTEXT;
  }

  spanContext(): SpanContext {
    return this.#spanContext;
  }

  setAttribute(_key: string, _value: unknown): this {
    return this;
  }

  setAttributes(_attributes: Record<string, unknown>): this {
    return this;
  }

  addEvent(
    _name: string,
    _attributesOrTime?: unknown,
    _startTime?: unknown
  ): this {
    return this;
  }

  addLink(_link: unknown): this {
    return this;
  }

  addLinks(_links: unknown[]): this {
    return this;
  }

  setStatus(_status: SpanStatus): this {
    return this;
  }

  updateName(_name: string): this {
    return this;
  }

  end(_endTime?: unknown): void {
    // no-op
  }

  isRecording(): boolean {
    return false;
  }

  recordException(_exception: unknown, _time?: unknown): void {
    // no-op
  }
}

// ---- Span ----

type NativeSpan = ReturnType<typeof tracing.startSpan>;

class WorkerdSpan {
  #native: NativeSpan | null;
  #ended = false;

  constructor(native: NativeSpan) {
    this.#native = native;
  }

  spanContext(): SpanContext {
    // Phase 1a will expose real IDs from the C++ layer.
    // For now, return the invalid placeholder.
    return INVALID_SPAN_CONTEXT;
  }

  setAttribute(key: string, value: unknown): this {
    if (this.#ended) return this;
    if (
      typeof value === 'string' ||
      typeof value === 'number' ||
      typeof value === 'boolean'
    ) {
      this.#native!.setAttribute(key, value);
    }
    return this;
  }

  setAttributes(attributes: Record<string, unknown>): this {
    for (const [k, v] of Object.entries(attributes)) {
      this.setAttribute(k, v);
    }
    return this;
  }

  addEvent(
    _name: string,
    _attributesOrTime?: unknown,
    _startTime?: unknown
  ): this {
    // No-op for prototype — Phase 2c will implement
    return this;
  }

  addLink(_link: unknown): this {
    // No-op for prototype — Phase 3a will implement
    return this;
  }

  addLinks(_links: unknown[]): this {
    return this;
  }

  setStatus(_status: SpanStatus): this {
    // No-op for prototype — Phase 2a will implement
    return this;
  }

  updateName(_name: string): this {
    // No-op for prototype — Phase 2d will implement
    return this;
  }

  end(_endTime?: unknown): void {
    if (!this.#ended) {
      this.#native!.end();
      this.#ended = true;
      this.#native = null;
    }
  }

  isRecording(): boolean {
    return !this.#ended;
  }

  recordException(_exception: unknown, _time?: unknown): void {
    // No-op for prototype — Phase 2c will implement as addEvent('exception', ...)
  }
}

// ---- Tracer ----

class WorkerdTracer {
  constructor(_name: string, _version?: string) {
    // name and version will be used for instrumentationScope in Phase 2+.
    // For now, the C++ layer doesn't support passing tracer identity through.
  }

  startSpan(
    name: string,
    _options?: unknown,
    _context?: OtelContext
  ): WorkerdSpan {
    const native = tracing.startSpan(name);
    return new WorkerdSpan(native);
  }

  startActiveSpan(name: string, ...args: unknown[]): unknown {
    // The OTel API has an overloaded signature:
    //   startActiveSpan(name, fn)
    //   startActiveSpan(name, options, fn)
    //   startActiveSpan(name, options, context, fn)
    let fn: Function;
    let options: unknown;
    let parentCtx: OtelContext | undefined;

    if (args.length === 1) {
      fn = args[0] as Function;
    } else if (args.length === 2) {
      options = args[0];
      fn = args[1] as Function;
    } else {
      options = args[0];
      parentCtx = args[1] as OtelContext;
      fn = args[2] as Function;
    }

    const span = this.startSpan(name, options, parentCtx);
    const ctx = setSpanInContext(parentCtx ?? contextManager.active(), span);

    return contextManager.with(ctx, () => {
      try {
        return fn(span);
      } catch (e) {
        span.recordException(e);
        span.setStatus({ code: SpanStatusCode.ERROR });
        throw e;
      }
    });
  }
}

// ---- TracerProvider ----

class WorkerdTracerProvider {
  getTracer(name: string, version?: string, _options?: unknown): WorkerdTracer {
    return new WorkerdTracer(name, version);
  }
}

// ---- API singletons ----
//
// These mirror the shape of @opentelemetry/api's top-level exports.
// The `trace` and `context` objects are the "registration API" that the
// npm package expects to find on the global symbol.

const tracerProvider = new WorkerdTracerProvider();

export const trace = {
  getTracer(name: string, version?: string, options?: unknown): WorkerdTracer {
    return tracerProvider.getTracer(name, version, options);
  },
  getTracerProvider(): WorkerdTracerProvider {
    return tracerProvider;
  },
  setGlobalTracerProvider(_provider: unknown): unknown {
    // No-op — we don't allow overriding our provider
    return tracerProvider;
  },
  disable(): void {
    // No-op
  },
};

export const context = contextManager;

// The propagation and diag APIs are left as no-ops. The @opentelemetry/api
// package falls back to its built-in no-ops when these are not registered
// on the global symbol.

export {
  ROOT_CONTEXT,
  INVALID_SPAN_CONTEXT,
  TraceFlags,
  NonRecordingSpan,
  WorkerdSpan,
  WorkerdTracer,
  WorkerdTracerProvider,
};

// Re-export the OtelContext class as "Context" for the public API
export { OtelContext as Context };
