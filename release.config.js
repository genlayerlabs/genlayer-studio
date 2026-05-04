module.exports = {
    branches: ['main'],
    "plugins": [
        [
            "@semantic-release/commit-analyzer",
            {
                "preset": "conventionalcommits",
                // The first rule caps commits with a BREAKING CHANGE footer or `!:`
                // marker at `minor`. Without it, semantic-release's built-in default
                // rule `{ breaking: true, release: 'major' }` kicks in and auto-ships
                // a major release the moment any commit body contains the words
                // "BREAKING CHANGE". That's almost never what we want on 0.x — one
                // stray commit would jump us straight to 1.0.0. Run semantic-release
                // with an explicit `--release major` override for genuine major bumps.
                //
                // User rules are evaluated before the defaults; defaults only apply
                // when no user rule matches. So capturing `breaking: true` here
                // prevents the default major rule from being consulted at all.
                "releaseRules": [
                    { "breaking": true, "release": "minor" },
                    { "type": "feat", "release": "minor" },
                    { "type": "*", "release": "patch" },
                ],
            }
        ],
        [
            "@semantic-release/release-notes-generator",
            {
                "preset": "conventionalcommits",
                "presetConfig": {
                    "types": [
                        { "type": "feat", "section": "Features" },
                        { "type": "fix", "section": "Bug Fixes" },
                        { "type": "chore", "section": "Miscellaneous" },
                        { "type": "docs", "section": "Miscellaneous" },
                        { "type": "style", "section": "Miscellaneous" },
                        { "type": "refactor", "section": "Miscellaneous" },
                        { "type": "perf", "section": "Miscellaneous" },
                        { "type": "test", "section": "Miscellaneous" }
                    ]
                },
            }
        ],
        [
            "@semantic-release/npm",
            {
                "pkgRoot": "frontend",
                "npmPublish": false
            }
        ],
        '@semantic-release/github',
    ]
};
