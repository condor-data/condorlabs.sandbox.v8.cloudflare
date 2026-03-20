// Copyright (c) 2026 Cloudflare, Inc.
// Licensed under the Apache 2.0 license found in the LICENSE file or at:
//     https://opensource.org/licenses/Apache-2.0

import assert from 'node:assert';

// ---- Test: Symbol.for() bootstrap ----
//
// The bootstrap preamble should have registered our providers on the well-known
// global symbol before this module was evaluated.

export const bootstrapRegistered = {
  async test() {
    const otel = globalThis[Symbol.for('opentelemetry.js.api.1')];
    assert(otel, 'OTel global symbol should be registered');
    assert.strictEqual(
      otel.version,
      '1.999.999',
      'Version should be 1.999.999'
    );
    assert(otel.trace, 'trace provider should be registered');
    assert(otel.context, 'context manager should be registered');
  },
};

// ---- Test: TracerProvider / Tracer / Span via the global symbol ----
//
// This simulates what @opentelemetry/api does internally — it reads from the
// global symbol to get the TracerProvider.

export const createSpanViaGlobal = {
  async test() {
    const otel = globalThis[Symbol.for('opentelemetry.js.api.1')];
    const tracer = otel.trace.getTracer('test-app', '1.0.0');
    assert(tracer, 'getTracer() should return a tracer');

    const span = tracer.startSpan('test-operation');
    assert(span, 'startSpan() should return a span');
    assert.strictEqual(span.isRecording(), true, 'span should be recording');

    span.setAttribute('test', 'createSpanViaGlobal');
    span.setAttribute('string-attr', 'hello');
    span.setAttribute('number-attr', 42);
    span.setAttribute('bool-attr', true);

    span.end();
    assert.strictEqual(
      span.isRecording(),
      false,
      'span should not be recording after end()'
    );

    // Calling end() again should be a no-op (no error)
    span.end();
  },
};

// ---- Test: startActiveSpan ----
//
// Verifies that startActiveSpan calls the function with the span and returns
// the function's result.

export const startActiveSpan = {
  async test() {
    const otel = globalThis[Symbol.for('opentelemetry.js.api.1')];
    const tracer = otel.trace.getTracer('test-app');

    const result = tracer.startActiveSpan('active-span-test', (span) => {
      assert(span, 'fn should receive a span');
      assert.strictEqual(span.isRecording(), true);
      span.setAttribute('test', 'startActiveSpan');
      span.end();
      return 'active-result';
    });

    assert.strictEqual(result, 'active-result', 'should return fn result');
  },
};

// ---- Test: startActiveSpan with async function ----

export const startActiveSpanAsync = {
  async test() {
    const otel = globalThis[Symbol.for('opentelemetry.js.api.1')];
    const tracer = otel.trace.getTracer('test-app');

    const result = await tracer.startActiveSpan(
      'async-span-test',
      async (span) => {
        assert.strictEqual(span.isRecording(), true);
        span.setAttribute('test', 'startActiveSpanAsync');
        await new Promise((resolve) => setTimeout(resolve, 10));
        span.end();
        return 'async-result';
      }
    );

    assert.strictEqual(result, 'async-result');
  },
};

// ---- Test: startActiveSpan with error ----

export const startActiveSpanError = {
  async test() {
    const otel = globalThis[Symbol.for('opentelemetry.js.api.1')];
    const tracer = otel.trace.getTracer('test-app');

    let errorCaught = false;
    try {
      tracer.startActiveSpan('error-span-test', (span) => {
        span.setAttribute('test', 'startActiveSpanError');
        throw new Error('test error');
      });
    } catch (e) {
      errorCaught = true;
      assert.strictEqual(e.message, 'test error');
    }
    assert(errorCaught, 'Error should have been caught');
  },
};

// ---- Test: setAttributes (bulk) ----

export const setAttributesBulk = {
  async test() {
    const otel = globalThis[Symbol.for('opentelemetry.js.api.1')];
    const tracer = otel.trace.getTracer('test-app');

    tracer.startActiveSpan('bulk-attrs-test', (span) => {
      span.setAttribute('test', 'setAttributesBulk');
      span.setAttributes({
        'bulk.string': 'value',
        'bulk.number': 123,
        'bulk.bool': false,
      });
      span.end();
    });
  },
};

// ---- Test: no-op methods don't throw ----

export const noOpMethods = {
  async test() {
    const otel = globalThis[Symbol.for('opentelemetry.js.api.1')];
    const tracer = otel.trace.getTracer('test-app');

    tracer.startActiveSpan('noop-test', (span) => {
      // These are all no-ops in the prototype but shouldn't throw
      span.addEvent('event-name', { key: 'value' });
      span.addLink({ context: span.spanContext() });
      span.addLinks([]);
      span.setStatus({ code: 1 });
      span.updateName('new-name');
      span.recordException(new Error('test'));

      const ctx = span.spanContext();
      assert(ctx, 'spanContext() should return an object');
      assert.strictEqual(typeof ctx.traceId, 'string');
      assert.strictEqual(typeof ctx.spanId, 'string');
      assert.strictEqual(typeof ctx.traceFlags, 'number');

      span.end();
    });
  },
};

// ---- Test: Context API ----

export const contextApi = {
  async test() {
    const otel = globalThis[Symbol.for('opentelemetry.js.api.1')];
    const ctx = otel.context;

    assert(ctx, 'context manager should exist');
    const active = ctx.active();
    assert(active, 'active() should return a context');

    // Context is an immutable key-value bag
    const key = Symbol('test-key');
    const newCtx = active.setValue(key, 'test-value');
    assert.notStrictEqual(newCtx, active, 'setValue returns a new context');
    assert.strictEqual(
      newCtx.getValue(key),
      'test-value',
      'getValue returns the set value'
    );
    assert.strictEqual(
      active.getValue(key),
      undefined,
      'original context unchanged'
    );

    // with() propagates context synchronously
    const result = ctx.with(newCtx, () => {
      const innerCtx = ctx.active();
      assert.strictEqual(
        innerCtx.getValue(key),
        'test-value',
        'active() inside with() returns the pushed context'
      );
      return 'with-result';
    });
    assert.strictEqual(result, 'with-result');

    // After with() returns, active context is restored
    assert.strictEqual(
      ctx.active().getValue(key),
      undefined,
      'context restored after with()'
    );
  },
};
