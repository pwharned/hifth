import org.scalajs.linker.interface.ModuleSplitStyle

val scala3Version = "3.8.3"
val http4sVersion = "0.23.32"
val fs2Version = "3.13.0"
val laminarVersion = "17.2.1"
val jsoniterVersion = "2.38.10"
val scalajsDomVersion = "2.8.0"

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
  val repoDir = baseDirectory.value.getParentFile
  val verifiedDir = repoDir / "data" / "output" / "verified"
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
    log.info(s"Copied: ${src.getName} -> $dst")
  }
  log.info(s"Done: ${files.length} file(s) deployed to $targetDir")
}

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
    reStart / mainClass := Some("com.github.pwharned.hifth.backend.Main"),
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

lazy val root = project
  .in(file("."))
  .aggregate(sharedJVM, sharedJS, frontend, backend)
  .settings(
    name := "hifth",
    scalaVersion := scala3Version
  )
