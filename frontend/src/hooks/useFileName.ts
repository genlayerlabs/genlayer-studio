export const useFileName = () => {
  function cleanupFileName(name: string) {
    const tokens = name.split('.');
    if (tokens.length > 0) {
      return `${tokens[0]}.py`;
    }
    return `${name}.py`;
  }

  return {
    cleanupFileName,
  };
};
