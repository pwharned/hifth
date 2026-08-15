# Archipelago

A full-stack Scala 3 web application template built around the islands architecture pattern.

## What this is

The core idea: the server serves plain HTML pages. Interactive functionality is provided by small, independent ScalaJS modules ("islands") that mount into specific elements on the page. All client-server communication goes through a single WebSocket connection using a shared typed message protocol.

No REST endpoints. No SPA framework. No shared mutable state between islands except through the server.

## Stack

- **Backend**: http4s + cats-effect + fs2
- **Frontend**: ScalaJS + Laminar
- **Shared**: Scala 3 cross-compiled domain models and WebSocket protocol
- **Serialization**: jsoniter-scala

## Project structure

```
shared/     - domain models and WS message ADTs, compiled for JVM and JS
backend/    - http4s server, WebSocket handler, static asset serving
frontend/   - Laminar islands, compiled to ES modules
static/     - plain HTML files (not generated, not templated)
```

## How it works

The shared module defines the entire client-server contract:

```scala
enum ClientMessage:
  case Increment
  case Decrement
  case Reset

enum ServerMessage:
  case CountUpdated(value: Int)
  case Error(msg: String)
```

Both sides are bound to this ADT. Adding a new message type is a compile error until both sides handle it.

On the frontend, islands communicate exclusively through `AppBus`:

```scala
// send
AppBus.outgoing.emit(ClientMessage.Increment)

// receive
AppBus.incoming.events.collect:
  case ServerMessage.CountUpdated(v) => v
```

Islands know nothing about WebSockets, serialization or connection state. `WsClient` owns the connection and handles reconnection with exponential backoff. Disconnection is surfaced via CSS on `#app-container` - no connection logic leaks into domain islands.

## Running in development

```bash
# terminal 1 - recompile frontend on change
sbt ~frontend/fastLinkJS

# terminal 2 - run backend with filesystem asset serving
MODE=DEV sbt ~backend/reStart
```

Open `http://localhost:8080`.

The browser auto-reloads when the backend restarts via a lightweight SSE endpoint that only exists in dev mode.

## Production build

```bash
sbt backend/package
```

This compiles the frontend with full optimisation, copies the JS output into the backend jar alongside the HTML files, and produces a self-contained artifact. Set `MODE=PROD` (or omit `MODE` entirely) at runtime.

## Adding an island

1. Create a new file in `frontend/src/main/scala/.../islands/`
2. Export a mount function:

```scala
@JSExportTopLevel("mountMyIsland", moduleID = "myisland")
def mount(el: dom.Element): Unit = ...
```

3. Add a mount point in your HTML:

```html
<div id="my-island"></div>
<script type="module">
  import { mountMyIsland } from "/js/myisland.js";
  mountMyIsland(document.getElementById("my-island"));
</script>
```

4. Add message types to the shared protocol if needed.

No routing configuration. No new endpoints. The WS handler picks up new message types automatically once they are added to the ADT.

## Adding a backend handler

Implement `MessageHandler[IO, ClientMessage]` and wire it into `WsHandler.make` in `Main.scala`. The handler receives a `publish` function for sending messages to all connected clients and is otherwise free to depend on whatever it needs (database connections, HTTP clients, etc.).
