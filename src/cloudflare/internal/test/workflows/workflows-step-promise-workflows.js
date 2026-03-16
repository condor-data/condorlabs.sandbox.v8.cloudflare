// Copyright (c) 2024 Cloudflare, Inc.
// Licensed under the Apache 2.0 license found in the LICENSE file or at:
//     https://opensource.org/licenses/Apache-2.0

import { WorkflowEntrypoint } from 'cloudflare:workers';

// Exercises all the forwarding paths in a single workflow:
//   - step.do() without rollback
//   - step.do().rollback(fn)
//   - step.do().rollback(config, fn)
//   - step.sleep() / step.sleepUntil() passthrough
//   - step.waitForEvent().rollback(fn)
export class ForwardingWorkflow extends WorkflowEntrypoint {
  async run(event, step) {
    const basic = await step.do('basic', async () => 'basic-result');
    const withFn = await step
      .do('with-fn', async () => 'fn-result')
      .rollback(async ({ error, output }) => {});
    const withConfig = await step
      .do('with-config', async () => 'config-result')
      .rollback(
        {
          retries: { limit: 3, delay: '5 seconds', backoff: 'linear' },
          timeout: '30 seconds',
        },
        async ({ error, output }) => {}
      );
    await step.sleep('s1', '10 seconds');
    await step.sleepUntil('s2', new Date('2025-01-01'));
    const waited = await step
      .waitForEvent('my-wait', { type: 'approval', timeout: '5 minutes' })
      .rollback(async ({ output }) => {});
    return { basic, withFn, withConfig, waited };
  }
}

// Exercises all three error guards in a single workflow
export class ErrorGuardsWorkflow extends WorkflowEntrypoint {
  async run(event, step) {
    const errors = [];

    // .rollback() called twice
    try {
      await step
        .do('s1', async () => 'r')
        .rollback(async () => {})
        .rollback(async () => {});
    } catch (e) {
      errors.push(e.message);
    }

    // .rollback() after await
    const promise = step.do('s2', async () => 'r');
    await promise;
    try {
      promise.rollback(async () => {});
    } catch (e) {
      errors.push(e.message);
    }

    // .rollback(null)
    try {
      await step.do('s3', async () => 'r').rollback(null);
    } catch (e) {
      errors.push(e.message);
    }

    return { errors };
  }
}

// Step callback throws — error surfaces through StepPromise
export class ErrorPropagationWorkflow extends WorkflowEntrypoint {
  async run(event, step) {
    try {
      await step.do('fail', async () => {
        throw new Error('step-boom');
      });
      return { threw: false };
    } catch (e) {
      return { threw: true, message: e.message };
    }
  }
}

// Rollback fn callable over RPC — mock invokes it, verifies round-trip
export class RollbackCallableWorkflow extends WorkflowEntrypoint {
  async run(event, step) {
    const value = await step
      .do('my-step', async () => 'step-output')
      .rollback(async ({ error, output, stepName }) => {
        return {
          receivedError: error,
          receivedOutput: output,
          receivedStepName: stepName,
        };
      });
    return { value };
  }
}
