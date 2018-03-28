### albums
A list of CSV files containing popular/acclaimed albums used to make a list of albums to listen to.

This is a personal project of mine, but I thought I'd leave these CSV files up here in case anyone wanted to use them.

The files are bound to have errors in source files somewhere - duplicates due to slight differences in album names, these were scraped off a variety of websites.

The most accurate will be the one in root directory: `spreadsheets.csv`, the one I am currently using on [my spreadsheet](https://docs.google.com/spreadsheets/d/12htSAMg67czl8cpkj1mX0TuAFvqL_PJLI4hv1arG5-M/edit#gid=1451660661).

##### Sources for `spreadsheet.csv`:

* [1001 Albums You Must Hear Before You Die](https://en.wikipedia.org/wiki/1001_Albums_You_Must_Hear_Before_You_Die). I attempted to include any albums that have ever been in the book, multiple versions with different lineups have come out in 2005, 2008, 2010, 2011, and 2016.

* [Rolling Stone's 500 Greatest Albums of All Time](https://en.wikipedia.org/wiki/Rolling_Stone%27s_500_Greatest_Albums_of_All_Time). Likewise, the count here is 516, due to multiple versions.

* Wins from the four big [AMA](https://en.wikipedia.org/wiki/American_Music_Award) Categories relating to albums: 
    * [Favorite Pop/Rock Album](https://en.wikipedia.org/wiki/American_Music_Award_for_Favorite_Pop/Rock_Album)
    * [Favorite Soul/R&B Album](https://en.wikipedia.org/wiki/American_Music_Award_for_Favorite_Soul/R%26B_Album)
    * [Favorite Country Album](https://en.wikipedia.org/wiki/American_Music_Award_for_Favorite_Country_Album)
    * [Favorite Rap/Hip-Hop Album](https://en.wikipedia.org/wiki/American_Music_Award_for_Favorite_Rap/Hip-Hop_Album)

* Wins from a multitude (though not all) Grammy Awards, sources [here](https://github.com/seanbrecke/albums/tree/master/src/Grammy).

Both the [AMA](https://github.com/seanbrecke/albums/tree/master/src/AMA) and [Grammy](https://github.com/seanbrecke/albums/tree/master/src/Grammy) directories have an `all.csv` (nominations and wins) and `wins.csv` (just wins) file. 

The 'Year' column refers to date released. However, in source `csv` files for Grammy/AMA, the year is often the date awarded. I correct these as I listen to them in `spreadsheet.csv`.

###### `nextalbums.py`

A `python3.6` script used to interact with the sheets document and maintain `spreadsheets.csv`.

Dependencies: `pip3 install --upgrade google-api-python-client prettytable`

Setup: Run `python3 setup.py` to setup the OAuth2 client credentials.

Usage:

```
usage: nextalbums.py [-h] [-c COUNT] [-r] [-o] [--csv]

Get the Next Albums to listen to.

optional arguments:
  -h, --help                show this help message and exit
  -c COUNT, --count COUNT   Changes the number of albums to return. Default is 10.
  -r, --random              Chooses random albums instead of listing chronologically.
  -o, --open                Open the cell that corresponds to the next album in
                            the spreadsheet online. Ignored if choosing randomly.
  --csv                     Generates a CSV file without any scores/'listened on'
                            dates.
```

For example, to return the next 7 albums to listen to (chronologically), and open the cell that corresponds to <i>September of My Years</i> in a web browser:
```
$ python3.6 nextalbums.py -oc 7
+----------------------------+----------------+------+
| Album                      | Artist         | Year |
+----------------------------+----------------+------+
| September of My Years      | Frank Sinatra  | 1966 |
| The In Crowd               | Ramsey Lewis   | 1966 |
| The Return of Roger Miller | Roger Miller   | 1966 |
| A Man and His Music        | Frank Sinatra  | 1967 |
| Don't Come Home A-Drinkin' | Loretta Lynn   | 1967 |
| Goin' Out of My Head       | Wes Montgomery | 1967 |
| Caetano Veloso             | Caetano Veloso | 1968 |
+----------------------------+----------------+------+
```

[Basic tutorial for Google Sheets API](https://developers.google.com/sheets/api/quickstart/python).
