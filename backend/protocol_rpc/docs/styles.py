"""
CSS styles for the documentation system.
"""


def get_swagger_styles() -> str:
    """Get the Swagger-like CSS styles"""
    from .utils import get_method_category_styles

    # Generate dynamic category styles
    category_styles = get_method_category_styles()
    dynamic_category_css = ""
    for category, styles in category_styles.items():
        dynamic_category_css += f"""
        .method-badge.{category} {{ background: {styles['background']}; color: {styles['color']}; }}"""

    base_css = """
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        :root {
            /* GenLayer Studio Primary Colors */
            --primary: #1a3851;
            --primary-light: #2a4861;
            --primary-dark: #0a2841;
            --primary-50: #e8eef4;
            --primary-100: #d1dde9;
            --primary-200: #a3bbd3;
            --primary-300: #7599bd;
            --primary-500: #1a3851;
            --primary-600: #152d41;
            --primary-700: #102231;
            --primary-800: #0b1621;
            --primary-900: #050b11;

            /* Accent Colors (Sky) */
            --accent: #0ea5e9;
            --accent-50: #f0f9ff;
            --accent-100: #e0f2fe;
            --accent-200: #bae6fd;
            --accent-300: #7dd3fc;
            --accent-400: #38bdf8;
            --accent-500: #0ea5e9;
            --accent-600: #0284c7;
            --accent-700: #0369a1;
            --accent-800: #075985;
            --accent-900: #0c4a6e;

            /* Gray Scale */
            --gray-50: #f9fafb;
            --gray-100: #f3f4f6;
            --gray-200: #e5e7eb;
            --gray-300: #d1d5db;
            --gray-400: #9ca3af;
            --gray-500: #6b7280;
            --gray-600: #4b5563;
            --gray-700: #374151;
            --gray-800: #1f2937;
            --gray-900: #111827;

            /* Zinc Scale (for dark mode) */
            --zinc-50: #fafafa;
            --zinc-100: #f4f4f5;
            --zinc-200: #e4e4e7;
            --zinc-300: #d4d4d8;
            --zinc-400: #a1a1aa;
            --zinc-500: #71717a;
            --zinc-600: #52525b;
            --zinc-700: #3f3f46;
            --zinc-800: #27272a;
            --zinc-900: #18181b;

            /* Status Colors */
            --success-50: #f0fdf4;
            --success-500: #22c55e;
            --success-600: #16a34a;

            --error-50: #fef2f2;
            --error-500: #ef4444;
            --error-600: #dc2626;

            --warning-50: #fffbeb;
            --warning-500: #f59e0b;
            --warning-600: #d97706;

            --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
            --shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1);
            --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
            --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
            --shadow-xl: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1);

            --radius-sm: 0.375rem;
            --radius: 0.5rem;
            --radius-md: 0.75rem;
            --radius-lg: 1rem;
            --radius-xl: 1.5rem;

            --transition: all 100ms cubic-bezier(0.4, 0, 0.2, 1);
            --transition-colors: color 0.5s, background-color 0.5s, border-color 0.5s;
        }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--gray-50);
            color: var(--gray-900);
            line-height: 1.6;
            font-size: 15px;
            font-feature-settings: 'cv02', 'cv03', 'cv04', 'cv11';
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            min-height: 100vh;
        }

        /* Header */
        .header {
            background: var(--primary);
            color: white;
            padding: 0;
            position: sticky;
            top: 0;
            z-index: 100;
            box-shadow: var(--shadow);
        }

        .header-content {
            max-width: 1600px;
            margin: 0 auto;
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .logo {
            font-size: 1.5rem;
            font-weight: 700;
            color: white;
            text-decoration: none;
            letter-spacing: -0.025em;
            transition: var(--transition);
        }

        .logo:hover {
            opacity: 0.9;
        }

        .header-info {
            display: flex;
            gap: 1.5rem;
            align-items: center;
            font-size: 0.875rem;
            color: rgba(255, 255, 255, 0.9);
        }

        .base-url {
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            padding: 0.5rem 1rem;
            border-radius: var(--radius);
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 0.8125rem;
            font-weight: 500;
            color: white;
            backdrop-filter: blur(10px);
        }

        /* Search */
        .search-container {
            background: white;
            border-bottom: 1px solid var(--gray-300);
            padding: 1rem 0;
            position: sticky;
            top: 64px;
            z-index: 50;
        }

        .search-wrapper {
            max-width: 1600px;
            margin: 0 auto;
            padding: 0 2rem;
        }

        .search-box {
            width: 100%;
            padding: 0.75rem 1rem 0.75rem 2.75rem;
            font-size: 0.875rem;
            border: 1px solid var(--gray-300);
            border-radius: var(--radius);
            background: white;
            transition: var(--transition);
            position: relative;
            background-image: url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke-width='1.5' stroke='%236b7280'%3e%3cpath stroke-linecap='round' stroke-linejoin='round' d='m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z'/%3e%3c/svg%3e");
            background-repeat: no-repeat;
            background-position: 0.75rem center;
            background-size: 1.125rem;
        }

        .search-box:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px var(--accent-100);
        }

        .search-box::placeholder {
            color: var(--gray-400);
        }

        /* Main Content */
        .main-content {
            max-width: 1600px;
            margin: 0 auto;
            padding: 2rem;
            display: grid;
            grid-template-columns: 320px 1fr;
            gap: 2.5rem;
            align-items: start;
        }

        /* Sidebar */
        .sidebar {
            position: sticky;
            top: 140px;
            background: white;
            border-radius: var(--radius);
            border: 1px solid var(--gray-300);
            overflow: hidden;
            max-height: calc(100vh - 160px);
            overflow-y: auto;
        }

        .sidebar-header {
            padding: 1rem 1.5rem;
            border-bottom: 1px solid var(--gray-300);
            font-weight: 600;
            font-size: 0.875rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--gray-600);
            background: var(--gray-50);
        }

        .sidebar-content {
            padding: 1rem 0;
        }

        .category-section {
            margin-bottom: 0.75rem;
        }

        .category-header {
            padding: 0.75rem 1.5rem;
            font-weight: 600;
            font-size: 0.875rem;
            color: var(--gray-700);
            cursor: pointer;
            user-select: none;
            display: flex;
            align-items: center;
            justify-content: space-between;
            transition: var(--transition);
        }

        .category-header:hover {
            background: var(--gray-50);
            color: var(--primary);
        }

        .category-toggle {
            font-size: 0.75rem;
            color: var(--gray-500);
            transition: var(--transition);
        }

        .method-list {
            display: none;
            background: var(--gray-50);
        }

        .method-list.active {
            display: block;
        }

        .method-link {
            display: block;
            padding: 0.5rem 1.5rem 0.5rem 2.5rem;
            color: var(--gray-600);
            text-decoration: none;
            font-size: 0.8125rem;
            transition: var(--transition);
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
        }

        .method-link:hover {
            background: var(--gray-100);
            color: var(--gray-900);
        }

        .method-link.active {
            background: var(--accent);
            color: white;
            font-weight: 600;
        }

        /* Methods */
        .methods-container {
            background: white;
            border-radius: var(--radius);
            border: 1px solid var(--gray-300);
            overflow: hidden;
        }

        .method-group {
            border-bottom: 1px solid var(--gray-200);
        }

        .method-group:last-child {
            border-bottom: none;
        }

        .method-group-header {
            padding: 1.5rem 2rem;
            background: var(--gray-50);
            font-size: 1.125rem;
            font-weight: 600;
            color: var(--gray-800);
            border-bottom: 1px solid var(--gray-300);
        }

        .method {
            border-bottom: 1px solid var(--gray-200);
            transition: var(--transition);
        }

        .method:last-child {
            border-bottom: none;
        }

        .method-header {
            padding: 1.25rem 2rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 1rem;
            transition: var(--transition);
        }

        .method-header:hover {
            background: var(--gray-50);
        }

        .method.expanded .method-header {
            background: var(--gray-50);
        }

        .method-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 0.25rem 0.75rem;
            border-radius: var(--radius-sm);
            font-size: 0.6875rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            flex-shrink: 0;
        }

        .method-name {
            font-size: 0.9375rem;
            font-weight: 600;
            color: var(--gray-900);
            flex-grow: 1;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
        }

        .method-toggle {
            color: var(--gray-400);
            font-size: 1.125rem;
            transition: var(--transition);
        }

        .method.expanded .method-toggle {
            transform: rotate(90deg);
        }

        .method-content {
            display: none;
            padding: 0 2rem 2rem;
            background: white;
            border-top: 1px solid var(--gray-200);
        }

        .method.expanded .method-content {
            display: block;
        }

        .method-description {
            margin: 1.5rem 0;
            color: var(--gray-600);
            font-size: 0.875rem;
            line-height: 1.6;
        }

        /* Parameters */
        .parameters-section {
            margin: 2.5rem 0;
        }

        .section-title {
            font-size: 1.125rem;
            font-weight: 700;
            margin-bottom: 1.5rem;
            color: var(--gray-800);
            letter-spacing: -0.025em;
        }

        .parameters-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.875rem;
            border: 1px solid var(--gray-300);
            border-radius: var(--radius);
            overflow: hidden;
        }

        .parameters-table th {
            text-align: left;
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--gray-300);
            background: var(--gray-50);
            font-weight: 600;
            color: var(--gray-700);
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .parameters-table td {
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--gray-200);
            vertical-align: top;
            background: white;
        }

        .parameters-table tr:last-child td {
            border-bottom: none;
        }

        .param-name {
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-weight: 600;
            color: var(--gray-900);
            font-size: 0.8125rem;
        }

        .param-type {
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            color: var(--accent-700);
            font-size: 0.75rem;
        }

        .param-required {
            color: var(--error-600);
            font-weight: 600;
            font-size: 0.75rem;
        }

        .param-optional {
            color: var(--gray-500);
            font-size: 0.75rem;
        }

        .param-description {
            color: var(--gray-700);
            line-height: 1.6;
            font-weight: 500;
        }

        /* Try it out */
        .try-section {
            margin: 2rem 0;
            padding: 1.5rem;
            background: var(--gray-50);
            border: 1px solid var(--gray-300);
            border-radius: var(--radius);
        }

        .try-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
        }

        .try-button {
            background: var(--primary);
            color: white !important;
            border: none;
            padding: 0.5rem 1rem;
            border-radius: var(--radius);
            font-weight: 600;
            cursor: pointer;
            transition: var(--transition);
            font-size: 0.875rem;
        }

        .try-button:hover {
            background: var(--primary-light);
            color: white !important;
        }

        .try-button.cancel {
            background: var(--error-600);
            color: white !important;
        }

        .try-button.cancel:hover {
            background: var(--error-500);
            color: white !important;
        }

        .try-content {
            display: none;
        }

        .try-content.active {
            display: block;
        }

        .code-editor {
            margin: 1.5rem 0;
        }

        .code-editor-label {
            font-size: 0.9375rem;
            font-weight: 700;
            margin-bottom: 0.75rem;
            color: var(--gray-700);
        }

        .code-editor-input {
            width: 100%;
            min-height: 120px;
            padding: 1rem;
            border: 1px solid var(--gray-300);
            border-radius: var(--radius);
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 0.8125rem;
            resize: vertical;
            background: white;
            transition: var(--transition);
        }

        .code-editor-input:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px var(--accent-100);
        }

        .execute-button {
            background: var(--accent);
            color: white !important;
            border: none;
            padding: 0.625rem 1.25rem;
            border-radius: var(--radius);
            font-weight: 600;
            cursor: pointer;
            transition: var(--transition);
            font-size: 0.875rem;
            margin-top: 1rem;
        }

        .execute-button:hover {
            background: var(--accent-600);
            color: white !important;
        }

        /* Response */
        .response-section {
            margin-top: 2.5rem;
            display: none;
        }

        .response-section.active {
            display: block;
        }

        .response-header {
            display: flex;
            align-items: center;
            gap: 1.25rem;
            margin-bottom: 1.5rem;
        }

        .response-status {
            padding: 0.5rem 1rem;
            border-radius: var(--radius);
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            box-shadow: var(--shadow-sm);
        }

        .response-status.success {
            background: linear-gradient(135deg, var(--success-50) 0%, var(--success-100) 100%);
            color: var(--success-700);
            border: 1px solid var(--success-200);
        }

        .response-status.error {
            background: linear-gradient(135deg, var(--error-50) 0%, var(--error-100) 100%);
            color: var(--error-700);
            border: 1px solid var(--error-200);
        }

        .response-time {
            color: var(--gray-600);
            font-size: 0.875rem;
            font-weight: 600;
            background: var(--gray-100);
            padding: 0.25rem 0.75rem;
            border-radius: var(--radius);
        }

        .response-body {
            background: var(--gray-900);
            color: var(--gray-100);
            padding: 1rem;
            border-radius: var(--radius);
            overflow-x: auto;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 0.8125rem;
            line-height: 1.5;
            border: 1px solid var(--gray-700);
        }

        .response-body pre {
            margin: 0;
            white-space: pre-wrap;
            word-wrap: break-word;
            color: var(--gray-100);
        }

        /* Example */
        .example-section {
            margin: 2.5rem 0;
        }

        .example-code {
            background: var(--gray-900);
            color: var(--gray-100);
            padding: 1rem;
            border-radius: var(--radius);
            overflow-x: auto;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 0.8125rem;
            line-height: 1.5;
            border: 1px solid var(--gray-700);
        }

        .example-code pre {
            margin: 0;
            white-space: pre-wrap;
            word-wrap: break-word;
            color: var(--gray-100);
        }

        /* Returns */
        .returns-section {
            margin: 2.5rem 0;
        }

        .returns-type {
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            color: var(--accent-700);
            background: var(--accent-50);
            padding: 0.25rem 0.5rem;
            border-radius: var(--radius-sm);
            font-size: 0.8125rem;
            font-weight: 600;
            border: 1px solid var(--accent-200);
        }

        /* Loading */
        .loading {
            display: inline-block;
            width: 1rem;
            height: 1rem;
            border: 2px solid var(--gray-200);
            border-radius: 50%;
            border-top-color: var(--primary-500);
            animation: spin 0.8s linear infinite;
            margin-left: 0.5rem;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        /* Responsive */
        @media (max-width: 1280px) {
            .main-content {
                grid-template-columns: 280px 1fr;
                gap: 2rem;
            }

            .header-content {
                padding: 1rem 1.5rem;
            }

            .main-content {
                padding: 1.5rem;
            }
        }

        @media (max-width: 1024px) {
            .main-content {
                grid-template-columns: 1fr;
                gap: 1.5rem;
            }

            .sidebar {
                position: static;
                max-height: none;
                margin-bottom: 2rem;
                order: -1;
            }

            .header-content {
                flex-direction: column;
                gap: 1rem;
                text-align: center;
            }

            .search-container {
                position: static;
            }
        }

        @media (max-width: 768px) {
            .header-content {
                padding: 1rem;
            }

            .main-content {
                padding: 1rem;
            }

            .search-wrapper {
                padding: 0 1rem;
            }

            .method-header {
                padding: 1.25rem 1.5rem;
                flex-wrap: wrap;
                gap: 0.75rem;
            }

            .method-content {
                padding: 0 1.5rem 2rem;
            }

            .try-section {
                padding: 1.5rem;
            }

            .parameters-table {
                font-size: 0.875rem;
            }

            .parameters-table th,
            .parameters-table td {
                padding: 0.75rem 1rem;
            }
        }

        /* Scrollbar */
        ::-webkit-scrollbar {
            width: 10px;
            height: 10px;
        }

        ::-webkit-scrollbar-track {
            background: var(--gray-100);
            border-radius: var(--radius);
        }

        ::-webkit-scrollbar-thumb {
            background: linear-gradient(135deg, var(--gray-300) 0%, var(--gray-400) 100%);
            border-radius: var(--radius);
            border: 2px solid var(--gray-100);
        }

        ::-webkit-scrollbar-thumb:hover {
            background: linear-gradient(135deg, var(--gray-400) 0%, var(--gray-500) 100%);
        }

        /* Additional CSS custom properties */
        button {
            font-family: inherit;
        }

        /* Ensure good contrast on method badges */
        .method-badge {
            font-weight: 600;
        }
    """

    return base_css + dynamic_category_css
