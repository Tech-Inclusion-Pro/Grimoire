/// <reference types="vite/client" />

// TypeScript 6 rejects side-effect imports without a declaration, so CSS
// imports need one explicitly.
declare module '*.css' {}
