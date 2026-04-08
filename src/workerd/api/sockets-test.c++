#include "sockets.h"

#include <workerd/io/io-context.h>
#include <workerd/io/worker-interface.h>
#include <workerd/io/worker.h>
#include <workerd/tests/test-fixture.h>
#include <workerd/util/autogate.h>

#include <kj/test.h>

namespace workerd::api {
namespace {

// Minimal WorkerInterface that tracks when connect() is called.
class MockConnectWorkerInterface final: public WorkerInterface {
 public:
  MockConnectWorkerInterface(bool& connectCalled, kj::HttpHeaderTable& headerTable)
      : connectCalled(connectCalled),
        headerTable(headerTable) {}

  kj::Promise<void> connect(kj::StringPtr host,
      const kj::HttpHeaders& headers,
      kj::AsyncIoStream& connection,
      ConnectResponse& response,
      kj::HttpConnectSettings settings) override {
    connectCalled = true;
    kj::HttpHeaders responseHeaders(headerTable);
    response.accept(200, "OK"_kj, responseHeaders);
    return kj::NEVER_DONE;
  }

  kj::Promise<void> request(kj::HttpMethod method,
      kj::StringPtr url,
      const kj::HttpHeaders& headers,
      kj::AsyncInputStream& requestBody,
      kj::HttpService::Response& response) override {
    KJ_UNIMPLEMENTED("not used in this test");
  }
  kj::Promise<void> prewarm(kj::StringPtr url) override {
    KJ_UNIMPLEMENTED("not used in this test");
  }
  kj::Promise<ScheduledResult> runScheduled(kj::Date scheduledTime, kj::StringPtr cron) override {
    KJ_UNIMPLEMENTED("not used in this test");
  }
  kj::Promise<AlarmResult> runAlarm(kj::Date scheduledTime, uint32_t retryCount) override {
    KJ_UNIMPLEMENTED("not used in this test");
  }
  kj::Promise<CustomEvent::Result> customEvent(kj::Own<CustomEvent> event) override {
    return event->notSupported();
  }

 private:
  bool& connectCalled;
  kj::HttpHeaderTable& headerTable;
};

struct ConnectTestIoChannelFactory final: public TestFixture::DummyIoChannelFactory {
  ConnectTestIoChannelFactory(
      TimerChannel& timer, bool& connectCalled, kj::HttpHeaderTable& headerTable)
      : DummyIoChannelFactory(timer),
        connectCalled(connectCalled),
        headerTable(headerTable) {}

  kj::Own<WorkerInterface> startSubrequest(uint channel, SubrequestMetadata metadata) override {
    return kj::heap<MockConnectWorkerInterface>(connectCalled, headerTable);
  }

  bool& connectCalled;
  kj::HttpHeaderTable& headerTable;
};

KJ_TEST("connectImplNoOutputLock defers connect until output gate clears") {
  bool connectCalled = false;
  kj::HttpHeaderTable headerTable;

  Worker::Actor::Id actorId = kj::str("test-actor");
  TestFixture fixture(TestFixture::SetupParams{
    .actorId = kj::mv(actorId),
    .useRealTimers = false,
    .ioChannelFactory = [&](TimerChannel& timer) -> kj::Own<IoChannelFactory> {
    return kj::heap<ConnectTestIoChannelFactory>(timer, connectCalled, headerTable);
  },
  });

  util::Autogate::initAutogateNamesForTest({"tcp-socket-connect-output-gate"_kj});
  KJ_DEFER(util::Autogate::deinitAutogate());

  fixture.runInIoContext([&](const TestFixture::Environment& env) -> kj::Promise<void> {
    auto& actor = env.context.getActorOrThrow();
    auto paf = kj::newPromiseAndFulfiller<void>();
    auto blocker = actor.getOutputGate().lockWhile(kj::mv(paf.promise), nullptr);

    auto socket = connectImplNoOutputLock(env.js, kj::none, kj::str("localhost:1234"), kj::none);

    co_await kj::evalLater([]() {});
    KJ_EXPECT(!connectCalled, "connect must not happen while output gate is locked");

    paf.fulfiller->fulfill();
    co_await kj::evalLater([]() {});
    KJ_EXPECT(connectCalled, "connect must happen after output gate releases");
  });
}

KJ_TEST("connectImplNoOutputLock connects synchronously without autogate") {
  bool connectCalled = false;
  kj::HttpHeaderTable headerTable;

  Worker::Actor::Id actorId = kj::str("test-actor");
  TestFixture fixture(TestFixture::SetupParams{
    .actorId = kj::mv(actorId),
    .useRealTimers = false,
    .ioChannelFactory = [&](TimerChannel& timer) -> kj::Own<IoChannelFactory> {
    return kj::heap<ConnectTestIoChannelFactory>(timer, connectCalled, headerTable);
  },
  });

  fixture.runInIoContext([&](const TestFixture::Environment& env) -> kj::Promise<void> {
    auto& actor = env.context.getActorOrThrow();
    auto paf = kj::newPromiseAndFulfiller<void>();
    auto blocker = actor.getOutputGate().lockWhile(kj::mv(paf.promise), nullptr);

    auto socket = connectImplNoOutputLock(env.js, kj::none, kj::str("localhost:1234"), kj::none);
    KJ_EXPECT(connectCalled, "without autogate, connect must happen synchronously");

    paf.fulfiller->fulfill();
    co_await kj::evalLater([]() {});
  });
}

}  // namespace
}  // namespace workerd::api
