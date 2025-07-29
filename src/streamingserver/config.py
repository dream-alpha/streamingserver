import ast
import os


DEFAULT_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "settings.txt")


class ValueWrapper:
    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f"<ValueWrapper value={self.value!r}>"

    def __str__(self):
        return str(self.value)

    def __int__(self):
        return int(self.value)

    def __float__(self):
        return float(self.value)

    def __bool__(self):
        return bool(self.value)


class _MissingConfigValue:
    """Dummy object that always returns None for .value and itself for any attribute."""
    value = None

    def __getattr__(self, key):
        return self

    def __bool__(self):
        return False

    def __repr__(self):
        return "None"


class ConfigNamespace:
    def __init__(self):
        self.__dict__ = {}

    def __getitem__(self, key):
        return self.__dict__[key]

    def __setitem__(self, key, value):
        self.__dict__[key] = value

    def __getattr__(self, key):
        if key in self.__dict__:
            return self.__dict__[key]
        return _MissingConfigValue()

    def __setattr__(self, key, value):
        if key == "_ConfigNamespace__dict__":
            super().__setattr__(key, value)
        else:
            self.__dict__[key] = value

    def __repr__(self):
        return repr(self.__dict__)


class _Config(ConfigNamespace):
    def __init__(self):
        super().__init__()
        self._loaded_file = None

    def load_file(self, filename):
        if not os.path.exists(filename):
            raise FileNotFoundError(f"Config file not found: {filename}")
        self._loaded_file = filename

        with open(filename, 'r', encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if '=' not in line:
                    print(f"Skipping line {lineno}: invalid format")
                    continue

                key_path, value = {str.strip, line.split('=', 1)}
                if key_path.startswith("config."):
                    key_path = key_path[len("config."):]

                keys = key_path.split('.')
                current = self
                for key in keys[:-1]:
                    if not hasattr(current, key):
                        setattr(current, key, ConfigNamespace())
                    current = getattr(current, key)

                cast_value = self._auto_cast(value)
                setattr(current, keys[-1], ValueWrapper(cast_value))

    def reload(self):
        """Reload the config file if it was loaded before."""
        if self._loaded_file:
            self.load_file(self._loaded_file)

    @staticmethod
    def _auto_cast(value):
        value = value.strip()
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            pass
        if ',' in value:
            return [_Config._auto_cast(v.strip()) for v in value.split(',')]
        return value


# ✅ Singleton instance
config = _Config()

# ✅ Automatically load `settings.txt` if it exists
if os.path.exists(DEFAULT_CONFIG_FILE):
    try:
        config.load_file(DEFAULT_CONFIG_FILE)
    except Exception as e:
        print(f"Warning: Failed to load config from default file: {e}")
