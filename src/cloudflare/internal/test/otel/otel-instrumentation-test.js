// Copyright (c) 2026 Cloudflare, Inc.
// Licensed under the Apache 2.0 license found in the LICENSE file or at:
//     https://opensource.org/licenses/Apache-2.0

import assert from 'node:assert';
import {
  createTailStreamCollector,
  groupSpansBy,
} from 'instrumentation-test-helper';

// Create the collector and export it as the tail worker's default handler
const collector = createTailStreamCollector();
export default collector;

// After all tests complete, validate the spans that flowed through
export const validateSpans = {
  async test() {
    // Wait for all the tailStream invocations to finish
    await collector.waitForCompletion();

    // Get all spans and group by the 'test' attribute
    const allSpans = collector.spans.values();
    const spansByTest = groupSpansBy(allSpans, 'test');

    // Each test that creates spans should have set a 'test' attribute
    // to identify which test produced it.
    const testValidations = [
      { test: 'createSpanViaGlobal', expectedSpan: 'test-operation' },
      { test: 'startActiveSpan', expectedSpan: 'active-span-test' },
      { test: 'startActiveSpanAsync', expectedSpan: 'async-span-test' },
      { test: 'startActiveSpanError', expectedSpan: 'error-span-test' },
      { test: 'setAttributesBulk', expectedSpan: 'bulk-attrs-test' },
    ];

    for (const { test, expectedSpan } of testValidations) {
      const testSpans = spansByTest.get(test) || [];
      const span = testSpans.find((s) => s.name === expectedSpan);

      assert(span, `${test}: Should have created span '${expectedSpan}'`);
      assert(span.closed, `${test}: Span '${expectedSpan}' should be closed`);
    }

    // Validate specific attributes on the createSpanViaGlobal span
    const globalSpans = spansByTest.get('createSpanViaGlobal') || [];
    const globalSpan = globalSpans.find((s) => s.name === 'test-operation');
    assert(globalSpan, 'createSpanViaGlobal span should exist');
    assert.strictEqual(globalSpan['string-attr'], 'hello');
    assert.strictEqual(globalSpan['number-attr'], 42);
    assert.strictEqual(globalSpan['bool-attr'], true);

    // Validate bulk attributes
    const bulkSpans = spansByTest.get('setAttributesBulk') || [];
    const bulkSpan = bulkSpans.find((s) => s.name === 'bulk-attrs-test');
    assert(bulkSpan, 'setAttributesBulk span should exist');
    assert.strictEqual(bulkSpan['bulk.string'], 'value');
    assert.strictEqual(bulkSpan['bulk.number'], 123);
    assert.strictEqual(bulkSpan['bulk.bool'], false);

    console.log('All OTel instrumentation tests passed!');
  },
};
