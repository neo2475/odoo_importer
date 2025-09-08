from adapters import detect_adapter


def test_detection_grupo_pena():
    txt = "Factura GRUPO PEÑA AUTOMOCION, S.L. ..."
    cls = detect_adapter(txt, "GPA_0001.pdf")
    assert cls.key == "grupo_pena"


def test_detection_varona():
    txt = "Albarán VARONA 2008, S.L. ..."
    cls = detect_adapter(txt, "VA020001.pdf")
    assert cls.key == "varona"
