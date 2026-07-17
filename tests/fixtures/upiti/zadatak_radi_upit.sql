SELECT Radi.Procenat, Radi.IDPro, Zaposleni.Ime
FROM Radi, Zaposleni
WHERE Zaposleni.IDZap = Radi.IDZap AND Radi.Procenat > 99 AND Zaposleni.Ime = 'Ivan'
ORDER BY Radi.Procenat;
