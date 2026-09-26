# Legacy Hifth memorization app

This directory contains the original Quran memorization application. It is an
independent SBT build retained for legacy use and is not part of the root media
reviewer build.

```bash
cd hifth
sbt test
MODE=DEV sbt backend/reStart
```

The build contains the Archipelago-style `shared`, `frontend`, and `backend`
projects. Generated Scala.js files are written to `hifth/static/js`. Quran audio
and alignment pipeline data remain in the repository-level `data/` directory.
