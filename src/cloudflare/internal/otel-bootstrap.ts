// Copyright (c) 2026 Cloudflare, Inc.
// Licensed under the Apache 2.0 license found in the LICENSE file or at:
//     https://opensource.org/licenses/Apache-2.0

// Bootstrap preamble for OpenTelemetry API compatibility.
//
// This module is eagerly resolved before the main worker module, so that by
// the time user code runs `import { trace } from '@opentelemetry/api'`, the
// npm package discovers workerd's implementations via the well-known global
// symbol and uses them instead of its built-in no-ops.
//
// The `@opentelemetry/api` package checks for:
//   globalThis[Symbol.for('opentelemetry.js.api.1')]
// If present AND its `.version` is >= the package's version, the package
// reuses the registered providers.
//
// We set version to "1.999.999" — high enough that any 1.x version of the
// npm package will accept our providers without overwriting them.

import { trace, context } from 'cloudflare-internal:otel';

const OTEL_GLOBAL_KEY = Symbol.for('opentelemetry.js.api.1');
const OTEL_API_COMPAT_VERSION = '1.999.999';

// The global registry object. The npm package stores providers on this object
// using property names like 'trace', 'context', 'propagation', 'diag'.
interface OtelGlobalRegistration {
  version: string;
  trace?: unknown;
  context?: unknown;
}

const g = globalThis as unknown as Record<symbol, OtelGlobalRegistration>;
const otel: OtelGlobalRegistration = (g[OTEL_GLOBAL_KEY] ??= {
  version: OTEL_API_COMPAT_VERSION,
});

// Register our providers. If the object already existed (shouldn't happen
// since this preamble runs first), we still overwrite with our implementations.
otel.version = OTEL_API_COMPAT_VERSION;
otel.trace = trace;
otel.context = context;
