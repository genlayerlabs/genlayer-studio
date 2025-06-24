"""
HTML templates for the documentation system.
"""


def get_html_template() -> str:
    """Get the main HTML template structure"""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GenLayer Studio JSON-RPC API</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>{styles}</style>
</head>
<body>
    <header class="header">
        <div class="header-content">
            <a href="#" class="logo">GenLayer Studio JSON-RPC API</a>
            <div class="header-info">
                <span class="base-url" id="baseUrlDisplay"></span>
            </div>
        </div>
    </header>

    <div class="search-container">
        <div class="search-wrapper">
            <input type="text" class="search-box" id="searchBox" placeholder="Search endpoints...">
        </div>
    </div>

    <div class="main-content">
        <aside class="sidebar">
            <div class="sidebar-header">API Endpoints</div>
            <div class="sidebar-content" id="sidebarContent"></div>
        </aside>

        <main class="methods-container" id="methodsContainer"></main>
    </div>

    <script>{javascript}</script>
</body>
</html>"""
