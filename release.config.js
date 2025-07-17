module.exports = {
    branches: ['main'],
    "plugins": [
        [
            "@semantic-release/commit-analyzer",
            {
                "preset": "conventionalcommits",
                "releaseRules": [
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
        '@semantic-release/github',
    ]
};
