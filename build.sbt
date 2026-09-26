val scala3Version = "3.8.3"
val http4sVersion = "0.23.32"
val catsEffectVersion = "3.7.0"
val fs2Version = "3.13.0"
val laminarVersion = "17.2.1"
val jsoniterVersion = "2.38.10"
val scalajsDomVersion = "2.8.0"
val munitVersion = "1.1.1"

lazy val sharedDependencies = Def.setting(
  Seq(
    "com.github.plokhotnyuk.jsoniter-scala" %%% "jsoniter-scala-core" % jsoniterVersion,
    "com.github.plokhotnyuk.jsoniter-scala" %%% "jsoniter-scala-macros" % jsoniterVersion
  )
)

lazy val shared = crossProject(JSPlatform, JVMPlatform)
  .crossType(CrossType.Pure)
  .in(file("media-reviewer/shared"))
  .settings(
    name := "media-reviewer-shared",
    scalaVersion := scala3Version,
    libraryDependencies ++= sharedDependencies.value ++ Seq(
      "org.scalameta" %%% "munit" % munitVersion % Test
    )
  )

lazy val sharedJVM = shared.jvm
lazy val sharedJS = shared.js

lazy val frontend = project
  .in(file("media-reviewer/frontend"))
  .dependsOn(sharedJS)
  .enablePlugins(ScalaJSPlugin)
  .settings(
    name := "media-reviewer-frontend",
    scalaVersion := scala3Version,
    scalaJSUseMainModuleInitializer := false,
    scalaJSLinkerConfig ~= (_.withModuleKind(ModuleKind.ESModule)),
    libraryDependencies ++= Seq(
      "com.raquo" %%% "laminar" % laminarVersion,
      "org.scala-js" %%% "scalajs-dom" % scalajsDomVersion
    )
  )

lazy val backend = project
  .in(file("media-reviewer/backend"))
  .dependsOn(sharedJVM)
  .enablePlugins(RevolverPlugin)
  .settings(
    name := "media-reviewer-backend",
    scalaVersion := scala3Version,
    Compile / mainClass := Some(
      "com.github.pwharned.flashcards.reviewer.backend.Main"
    ),
    reStart / mainClass := (Compile / mainClass).value,
    reStart / baseDirectory := (ThisBuild / baseDirectory).value,
    libraryDependencies ++= Seq(
      "org.typelevel" %% "cats-effect" % catsEffectVersion,
      "org.http4s" %% "http4s-ember-server" % http4sVersion,
      "org.http4s" %% "http4s-dsl" % http4sVersion,
      "co.fs2" %% "fs2-core" % fs2Version,
      "co.fs2" %% "fs2-io" % fs2Version,
      "org.scalameta" %% "munit" % munitVersion % Test
    ),
    Test / unmanagedResourceDirectories +=
      (ThisBuild / baseDirectory).value / "media-reviewer" / "shared" / "src" / "test" / "resources",
    Compile / resourceGenerators += Def.task {
      val _ = (frontend / Compile / fullLinkJS).value
      val jsDir =
        (frontend / Compile / fullLinkJS / scalaJSLinkerOutputDirectory).value
      val resourceDir =
        (Compile / resourceManaged).value / "static" / "js"
      IO.createDirectory(resourceDir)
      IO.copyDirectory(jsDir, resourceDir)
      IO.listFiles(resourceDir).toSeq
    }.taskValue
  )

lazy val root = project
  .in(file("."))
  .aggregate(sharedJVM, sharedJS, frontend, backend)
  .settings(
    name := "media-reviewer",
    scalaVersion := scala3Version
  )
