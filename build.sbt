import org.scalajs.linker.interface.ModuleSplitStyle
val scala3Version = "3.8.3"
val http4sVersion = "0.23.32"
val catsEffectVersion = "3.7.0"
val fs2Version = "3.13.0"
val laminarVersion = "17.2.1"
val jsoniterVersion = "2.38.10"
val scalajsDomVersion = "2.8.0"
val munitVersion = "1.1.1"
// shared deps cross-compiled for both JVM and JS
lazy val sharedDependencies = Def.setting(
  Seq(
    "com.github.plokhotnyuk.jsoniter-scala" %%% "jsoniter-scala-core" % jsoniterVersion,
    "com.github.plokhotnyuk.jsoniter-scala" %%% "jsoniter-scala-macros" % jsoniterVersion
  )
)
lazy val deployAlignmentData = taskKey[Unit](
  "Copies verified alignment JSON files into backend static resources."
)
deployAlignmentData := {
  val verifiedDir = baseDirectory.value / "data" / "output" / "verified"
  val targetDir =
    baseDirectory.value / "backend" / "src" / "main" / "resources" / "static" / "data" / "surah"
  if (!verifiedDir.exists) {
    sys.error(
      s"Verified output directory not found: $verifiedDir. Run the alignment pipeline first."
    )
  }
  val files = verifiedDir.listFiles.filter(_.getName.endsWith("_aligned.json"))
  if (files.isEmpty) {
    sys.error(s"No verified alignment files found in $verifiedDir.")
  }
  IO.createDirectory(targetDir)
  val log = streams.value.log
  files.foreach { src =>
    val dst = targetDir / src.getName
    IO.copyFile(src, dst)
    log.info(s"Copied: ${src.getName} → $dst")
  }
  log.info(
    s"Done: ${files.length} file(s) deployed to $targetDir"
  )
}
// ── shared ──────────────────────────────────────────────────────────────────
lazy val shared = crossProject(JSPlatform, JVMPlatform)
  .crossType(CrossType.Pure)
  .in(file("shared"))
  .settings(
    name := "hifth-shared",
    scalaVersion := scala3Version,
    libraryDependencies ++= sharedDependencies.value
  )
lazy val sharedJVM = shared.jvm
lazy val sharedJS = shared.js
// ── backend ─────────────────────────────────────────────────────────────────
lazy val backend = project
  .in(file("backend"))
  .dependsOn(sharedJVM)
  .enablePlugins(RevolverPlugin)
  .settings(
    name := "hifth-backend",
    scalaVersion := scala3Version,
    libraryDependencies ++= Seq(
      "org.http4s" %% "http4s-ember-server" % http4sVersion,
      "org.http4s" %% "http4s-dsl" % http4sVersion,
      "co.fs2" %% "fs2-core" % fs2Version
    ),
    reStart / mainClass := Some(
      "com.github.pwharned.hifth.backend.Main"
    ),
    reStart / baseDirectory := (ThisBuild / baseDirectory).value,

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
// ── frontend ─────────────────────────────────────────────────────────────────
lazy val frontend = project
  .in(file("frontend"))
  .dependsOn(sharedJS)
  .enablePlugins(ScalaJSPlugin)
  .settings(
    name := "hifth-frontend",
    scalaVersion := scala3Version,
    scalaJSUseMainModuleInitializer := false,
    scalaJSLinkerConfig ~= {
      _.withModuleKind(ModuleKind.ESModule)
        .withModuleSplitStyle(
          ModuleSplitStyle.SmallModulesFor(
            List("com.github.pwharned.hifth.frontend.islands")
          )
        )
    },
    libraryDependencies ++= Seq(
      "com.raquo" %%% "laminar" % laminarVersion,
      "org.scala-js" %%% "scalajs-dom" % scalajsDomVersion
    ) ++ sharedDependencies.value,
    Compile / fastLinkJS / scalaJSLinkerOutputDirectory :=
      (ThisBuild / baseDirectory).value / "static" / "js",
    Compile / fullLinkJS / scalaJSLinkerOutputDirectory :=
      (ThisBuild / baseDirectory).value / "static" / "js"
  )
// ── generic flashcards ────────────────────────────────────────────────────────
lazy val flashcardsShared = crossProject(JSPlatform, JVMPlatform)
  .crossType(CrossType.Pure)
  .in(file("flashcards/shared"))
  .settings(
    name := "hifth-flashcards-shared",
    scalaVersion := scala3Version,
    libraryDependencies ++= sharedDependencies.value ++ Seq(
      "org.scalameta" %%% "munit" % munitVersion % Test
    )
  )
lazy val flashcardsSharedJVM = flashcardsShared.jvm
lazy val flashcardsSharedJS = flashcardsShared.js

lazy val flashcardsUi = project
  .in(file("flashcards/ui"))
  .dependsOn(flashcardsSharedJS)
  .enablePlugins(ScalaJSPlugin)
  .settings(
    name := "hifth-flashcards-ui",
    scalaVersion := scala3Version,
    scalaJSUseMainModuleInitializer := false,
    libraryDependencies ++= Seq(
      "com.raquo" %%% "laminar" % laminarVersion,
      "org.scala-js" %%% "scalajs-dom" % scalajsDomVersion
    )
  )

lazy val mediaReviewerFrontend = project
  .in(file("media-reviewer/frontend"))
  .dependsOn(flashcardsSharedJS, flashcardsUi)
  .enablePlugins(ScalaJSPlugin)
  .settings(
    name := "hifth-media-reviewer-frontend",
    scalaVersion := scala3Version,
    scalaJSUseMainModuleInitializer := false,
    scalaJSLinkerConfig ~= (_.withModuleKind(ModuleKind.ESModule)),
    libraryDependencies ++= Seq(
      "com.raquo" %%% "laminar" % laminarVersion,
      "org.scala-js" %%% "scalajs-dom" % scalajsDomVersion
    )
  )

lazy val mediaReviewerBackend = project
  .in(file("media-reviewer/backend"))
  .dependsOn(flashcardsSharedJVM)
  .enablePlugins(RevolverPlugin)
  .settings(
    name := "hifth-media-reviewer-backend",
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
      (ThisBuild / baseDirectory).value / "flashcards" / "shared" / "src" / "test" / "resources",
    Compile / resourceGenerators += Def.task {
      val _ = (mediaReviewerFrontend / Compile / fullLinkJS).value
      val jsDir =
        (mediaReviewerFrontend / Compile / fullLinkJS /
          scalaJSLinkerOutputDirectory).value
      val resourceDir =
        (Compile / resourceManaged).value / "static" / "js"
      IO.createDirectory(resourceDir)
      IO.copyDirectory(jsDir, resourceDir)
      IO.listFiles(resourceDir).toSeq
    }.taskValue
  )

lazy val clausulaExtension = project
  .in(file("extension"))
  .dependsOn(flashcardsSharedJS, flashcardsUi)
  .enablePlugins(ScalaJSPlugin)
  .settings(
    name := "clausula-extension",
    scalaVersion := scala3Version,
    scalaJSUseMainModuleInitializer := true,
    scalaJSLinkerConfig ~= (_.withModuleKind(ModuleKind.NoModule)),
    libraryDependencies ++= Seq(
      "com.raquo" %%% "laminar" % laminarVersion,
      "org.scala-js" %%% "scalajs-dom" % scalajsDomVersion
    ),
    Compile / fastLinkJS / scalaJSLinkerOutputDirectory :=
      (ThisBuild / baseDirectory).value / "extension" / "dist" / "js",
    Compile / fullLinkJS / scalaJSLinkerOutputDirectory :=
      (ThisBuild / baseDirectory).value / "extension" / "dist" / "js"
  )
// ── root ─────────────────────────────────────────────────────────────────────
lazy val root = project
  .in(file("."))
  .aggregate(
    sharedJVM,
    sharedJS,
    backend,
    frontend,
    flashcardsSharedJVM,
    flashcardsSharedJS,
    flashcardsUi,
    mediaReviewerFrontend,
    mediaReviewerBackend,
    clausulaExtension
  )
  .settings(
    name := "hifth",
    scalaVersion := scala3Version
  )
