#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: authored-frontmatter
 * description: "A package that has not been published yet, so it has no manifest.json."
 * metadata:
 *   version: 0.1.0
 *   execution_model: steps_with_presentation
 * steps:
 *   probe:
 *     type: process.exec
 *     argv:
 *     - python3
 *     - -c
 *     - |2
 *       import subprocess
 *       subprocess.run(['delta-tool', '--version'], capture_output=True)
 *   other:
 *     type: process.exec
 *     depends_on:
 *     - probe
 *     argv:
 *     - sh
 *     - -c
 *     - echo done
 * ---
 */
