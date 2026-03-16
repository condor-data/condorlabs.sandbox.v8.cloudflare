// Copyright (c) 2024 Cloudflare, Inc.
// Licensed under the Apache 2.0 license found in the LICENSE file or at:
//     https://opensource.org/licenses/Apache-2.0

// TODO(cleanup): C++ built-in modules do not yet support named exports, so we must define this
//   wrapper module that simply re-exports the classes from the built-in module.

import entrypoints from 'cloudflare-internal:workers';
import innerEnv from 'cloudflare-internal:env';

export const WorkerEntrypoint = entrypoints.WorkerEntrypoint;
export const DurableObject = entrypoints.DurableObject;
export const RpcStub = entrypoints.RpcStub;
export const RpcPromise = entrypoints.RpcPromise;
export const RpcProperty = entrypoints.RpcProperty;
export const RpcTarget = entrypoints.RpcTarget;
export const ServiceStub = entrypoints.ServiceStub;

type RollbackFn = ((...args: unknown[]) => Promise<void>) | null;
type RollbackConfig = Record<string, unknown> | null;
type ExecuteFn = (
  rollbackFn: RollbackFn,
  rollbackConfig: RollbackConfig
) => Promise<unknown>;

// The step RPC stub interface — mirrors what the engine exposes via JS RPC.
interface StepRpcStub {
  do(...args: unknown[]): Promise<unknown>;
  sleep(name: string, duration: unknown): Promise<void>;
  sleepUntil(name: string, timestamp: unknown): Promise<void>;
  waitForEvent(...args: unknown[]): Promise<unknown>;
}

// StepPromise is a deferred thenable that captures .rollback() synchronously before firing
// the actual RPC. This is necessary because step.do() returns an RPC promise -- accessing
// .rollback() on it after resolution would be interpreted as a pipelined RPC call on the
// resolved value. By deferring execution until .then(), we can bundle the callback and rollback
// function in a single RPC call.
class StepPromise {
  #execute: ExecuteFn | null;
  #rollbackFn: RollbackFn = null;
  #rollbackConfig: RollbackConfig = null;
  #hasRollback = false;
  #promise: Promise<unknown> | null = null;
  #launched = false;

  get [Symbol.toStringTag](): string {
    return 'StepPromise';
  }

  constructor(execute: ExecuteFn) {
    this.#execute = execute;
  }

  rollback(
    configOrFn: RollbackConfig | RollbackFn,
    maybeFn?: RollbackFn
  ): StepPromise {
    if (this.#launched) {
      throw new Error('.rollback() must be called before the step is awaited');
    }
    if (this.#hasRollback) {
      throw new Error('.rollback() can only be called once per step');
    }
    this.#hasRollback = true;

    if (typeof configOrFn === 'function') {
      // rollback(fn)
      this.#rollbackFn = configOrFn;
    } else if (
      configOrFn !== null &&
      configOrFn !== undefined &&
      typeof configOrFn === 'object'
    ) {
      // rollback(config, fn)
      if (typeof maybeFn !== 'function') {
        throw new TypeError(
          '.rollback(config, fn) requires the second argument to be a function'
        );
      }
      this.#rollbackConfig = configOrFn;
      this.#rollbackFn = maybeFn;
    } else {
      throw new TypeError(
        '.rollback() expects a function, or a config object followed by a function'
      );
    }

    return this;
  }

  #getPromise(): Promise<unknown> {
    if (!this.#promise) {
      this.#launched = true;
      // #execute is non-null here because #promise is only set in this block.
      this.#promise = this.#execute!(this.#rollbackFn, this.#rollbackConfig);
      // Allow GC of the closure and rollback references now that the RPC has been dispatched.
      this.#execute = null;
      this.#rollbackFn = null;
      this.#rollbackConfig = null;
    }
    return this.#promise;
  }

  then(
    onFulfilled?: ((value: unknown) => unknown) | null,
    onRejected?: ((reason: unknown) => unknown) | null
  ): Promise<unknown> {
    return this.#getPromise().then(onFulfilled, onRejected);
  }

  catch(onRejected?: ((reason: unknown) => unknown) | null): Promise<unknown> {
    return this.#getPromise().catch(onRejected);
  }

  finally(onFinally?: (() => void) | null): Promise<unknown> {
    return this.#getPromise().finally(onFinally);
  }
}

// Wraps the step RPC stub so that step.do() and step.waitForEvent() return StepPromise instances.
// sleep() and sleepUntil() are passed through unchanged (they return Promise<void>, no rollback).
function wrapStep(jsStep: StepRpcStub): unknown {
  return {
    do(
      name: string,
      configOrCallback: unknown,
      maybeCallback?: unknown
    ): StepPromise {
      return new StepPromise(
        (rollbackFn: RollbackFn, rollbackConfig: RollbackConfig) => {
          // Build the args for the underlying RPC call.
          // Engine signature: do(name, configOrCallback, callback?, rollbackFn?, rollbackConfig?)
          const args: unknown[] = [name];
          if (maybeCallback !== undefined) {
            // do(name, config, callback) form
            args.push(configOrCallback, maybeCallback);
          } else {
            // do(name, callback) form
            args.push(configOrCallback);
          }
          if (rollbackFn) {
            args.push(rollbackFn);
            if (rollbackConfig) {
              args.push(rollbackConfig);
            }
          }
          return jsStep.do(...args);
        }
      );
    },

    sleep(name: string, duration: unknown): Promise<void> {
      return jsStep.sleep(name, duration);
    },

    sleepUntil(name: string, timestamp: unknown): Promise<void> {
      return jsStep.sleepUntil(name, timestamp);
    },

    waitForEvent(name: string, options: unknown): StepPromise {
      return new StepPromise(
        (rollbackFn: RollbackFn, rollbackConfig: RollbackConfig) => {
          // Engine signature: waitForEvent(name, options, rollbackFn?, rollbackConfig?)
          const args: unknown[] = [name, options];
          if (rollbackFn) {
            args.push(rollbackFn);
            if (rollbackConfig) {
              args.push(rollbackConfig);
            }
          }
          return jsStep.waitForEvent(...args);
        }
      );
    },
  };
}

// Wrap WorkflowEntrypoint to intercept run() calls and wrap the step argument before it reaches
// user code. This provides an extension point for step-level features (rollback, future additions)
// without modifying the C++ entrypoint.
//
// We use a JS subclass (not a Proxy) because the runtime walks the constructor prototype chain
// to classify entrypoints (workflowClasses vs actorClasses vs statelessClasses). A Proxy breaks
// identity comparison: `Proxy !== target`. A JS subclass preserves the chain:
//   UserWorkflow -> our JS class -> C++ WorkflowEntrypoint (matched by runtime)
const BaseWorkflowEntrypoint =
  entrypoints.WorkflowEntrypoint as unknown as new (
    ...args: unknown[]
  ) => Record<string, unknown>;

const kStepWrapped = Symbol('stepWrapped');

// Wraps a run function so that its second argument (step) is replaced with wrapStep(step).
function makeWrappedRun(
  originalRun: (event: unknown, step: unknown, ...rest: unknown[]) => unknown
): (event: unknown, step: unknown, ...rest: unknown[]) => unknown {
  return function (
    this: unknown,
    event: unknown,
    step: unknown,
    ...rest: unknown[]
  ): unknown {
    return originalRun.call(
      this,
      event,
      wrapStep(step as StepRpcStub),
      ...rest
    );
  };
}

class WorkflowEntrypointWrapper extends BaseWorkflowEntrypoint {
  constructor(...args: unknown[]) {
    super(...args);

    // Patch the run method on the subclass prototype if it defines its own run and hasn't been
    // patched yet. We use hasOwnProperty to avoid double-wrapping when a subclass inherits run
    // from a parent that was already patched (e.g. class B extends A extends WorkflowEntrypoint
    // where only A defines run).
    const proto = Object.getPrototypeOf(this) as Record<
      string | symbol,
      unknown
    >;
    if (
      proto &&
      Object.prototype.hasOwnProperty.call(proto, 'run') &&
      typeof proto.run === 'function' &&
      !proto[kStepWrapped]
    ) {
      const originalRun = proto.run as (
        event: unknown,
        step: unknown,
        ...rest: unknown[]
      ) => unknown;
      proto.run = makeWrappedRun(originalRun);
      proto[kStepWrapped] = true;
    }

    // NOTE: Arrow function class properties (e.g. `run = async () => {}`) are NOT supported.
    // They define `run` as an instance property, not a prototype method. The C++ RPC dispatch
    // resolves methods via the prototype chain, so arrow function `run` is invisible to RPC.
    // This is a workerd-level constraint that applies to all entrypoints (WorkerEntrypoint,
    // DurableObject, WorkflowEntrypoint).
  }
}

export const WorkflowEntrypoint = WorkflowEntrypointWrapper;

export function withEnv(newEnv: unknown, fn: () => unknown): unknown {
  return innerEnv.withEnv(newEnv, fn);
}

export function withExports(newExports: unknown, fn: () => unknown): unknown {
  return innerEnv.withExports(newExports, fn);
}

export function withEnvAndExports(
  newEnv: unknown,
  newExports: unknown,
  fn: () => unknown
): unknown {
  return innerEnv.withEnvAndExports(newEnv, newExports, fn);
}

// A proxy for the workers env/bindings. Since env is imported as a module-level
// reference, the object identity cannot be changed. The proxy provides indirection,
// delegating to different underlying env objects based on async context (see withEnv()).
// Mutations via this proxy modify the current underlying env object in-place - if you're
// inside a withEnv() scope, mutations affect the override object, not the base environment.
export const env = new Proxy(
  {},
  {
    get(_: unknown, prop: string | symbol): unknown {
      const inner = innerEnv.getCurrentEnv();
      if (inner) {
        return Reflect.get(inner, prop);
      }
      return undefined;
    },

    set(_: unknown, prop: string | symbol, newValue: unknown): boolean {
      const inner = innerEnv.getCurrentEnv();
      if (inner) {
        return Reflect.set(inner, prop, newValue);
      }
      return true;
    },

    has(_: unknown, prop: string | symbol): boolean {
      const inner = innerEnv.getCurrentEnv();
      if (inner) {
        return Reflect.has(inner, prop);
      }
      return false;
    },

    ownKeys(_: unknown): ArrayLike<string | symbol> {
      const inner = innerEnv.getCurrentEnv();
      if (inner) {
        return Reflect.ownKeys(inner);
      }
      return [];
    },

    deleteProperty(_: unknown, prop: string | symbol): boolean {
      const inner = innerEnv.getCurrentEnv();
      if (inner) {
        return Reflect.deleteProperty(inner, prop);
      }
      return true;
    },

    defineProperty(
      _: unknown,
      prop: string | symbol,
      attr: PropertyDescriptor
    ): boolean {
      const inner = innerEnv.getCurrentEnv();
      if (inner) {
        return Reflect.defineProperty(inner, prop, attr);
      }
      return true;
    },

    getOwnPropertyDescriptor(
      _: unknown,
      prop: string | symbol
    ): PropertyDescriptor | undefined {
      const inner = innerEnv.getCurrentEnv();
      if (inner) {
        return Reflect.getOwnPropertyDescriptor(inner, prop);
      }
      return undefined;
    },
  }
);

// A proxy for the worker exports. Since exports is imported as a module-level
// reference, the object identity cannot be changed. The proxy provides indirection,
// delegating to different underlying exports objects based on async context (see
// withExports()). This proxy is read-only - mutations are not supported.
export const exports = new Proxy(
  {},
  {
    get(_: unknown, prop: string | symbol): unknown {
      const inner = innerEnv.getCurrentExports();
      if (inner) {
        return Reflect.get(inner, prop);
      }
      return undefined;
    },

    has(_: unknown, prop: string | symbol): boolean {
      const inner = innerEnv.getCurrentExports();
      if (inner) {
        return Reflect.has(inner, prop);
      }
      return false;
    },

    ownKeys(_: unknown): ArrayLike<string | symbol> {
      const inner = innerEnv.getCurrentExports();
      if (inner) {
        return Reflect.ownKeys(inner);
      }
      return [];
    },

    getOwnPropertyDescriptor(
      _: unknown,
      prop: string | symbol
    ): PropertyDescriptor | undefined {
      const inner = innerEnv.getCurrentExports();
      if (inner) {
        return Reflect.getOwnPropertyDescriptor(inner, prop);
      }
      return undefined;
    },
  }
);

export const waitUntil = entrypoints.waitUntil.bind(entrypoints);
