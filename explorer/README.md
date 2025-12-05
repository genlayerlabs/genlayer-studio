# GenLayer Studio Explorer

A Next.js-based blockchain explorer for GenLayer Studio that provides visibility into transactions, validators, providers, and contract state.

## Features

- **Transaction Explorer**: View all transactions with detailed information including consensus data, monitoring timeline, and related transactions
- **Validators Dashboard**: Monitor active validators in the network
- **Providers Overview**: View configured LLM providers
- **State Inspector**: Browse and inspect contract state

## Prerequisites

- Node.js 18+ (recommended: Node.js 20+)
- npm, yarn, pnpm, or bun
- PostgreSQL database (typically provided by GenLayer Studio)
- GenLayer Studio running (for database access)

## Setup

### 1. Install Dependencies

```bash
npm install
# or
yarn install
# or
pnpm install
# or
bun install
```

### 2. Configure Environment Variables

Copy the sample environment file:

```bash
cp .env.sample .env
```

Edit `.env` with your database configuration:

```env
# Database Configuration
DB_HOST=localhost
DB_NAME=genlayer_state
DB_USER=postgres
DB_PASSWORD=postgres
DB_PORT=5432
```

> **Note**: The explorer connects to the same PostgreSQL database used by GenLayer Studio. Make sure GenLayer Studio is running and the database is accessible.

### 3. Start the Development Server

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## Available Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start development server with hot reload |
| `npm run build` | Build for production |
| `npm run start` | Start production server |
| `npm run lint` | Run ESLint |
