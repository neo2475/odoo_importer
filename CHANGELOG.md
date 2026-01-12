# CHANGELOG

## 0.1.1 – Refactor interno y compatibilidad

- Añadida función `detect_adapter` en `adapters/__init__.py` para mantener compatibilidad hacia atrás
  con código y tests que esperaban este punto de entrada. Internamente delega en `detect_provider`
  y `get_adapter`.
- Se mantiene el patrón de registro automático de adaptadores mediante el decorador `@register`.
- Ejecutada la batería de tests incluida (`tests/test_parsers.py`) y ajustada a la nueva API.
