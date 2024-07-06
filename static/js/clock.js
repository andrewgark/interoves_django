function doClock(serverTime, futureGames, ongoingGame) {
    // Calc offset from server time
    // Javascript time value is milliseconds since above epoch
    var serverOffset = serverTime - (new Date() / 1000 | 0);

    // Helper
    function z(n){return (n<10?'0':'') + n}

    function tickTock() {
        // Create a new Date object each time so
        // it doesn't matter if a second or more is skipped
        var now = new Date();

        // Adjust for server offset
        now.setSeconds(now.getSeconds() + serverOffset);
    
        // write clock to document, values are local, not UTC
        document.getElementById('clock').innerHTML = now.getFullYear()   + '-' +
                                                    z(now.getMonth()+1) + '-' +
                                                    z(now.getDate())    + ' ' +
                                                    z(now.getHours())   + ':' +
                                                    z(now.getMinutes()) + ':' +
                                                    z(now.getSeconds());

        // var newFutureGames = [];
        // for (var i = 0; i < futureGames.length; i++) {
        //     var game = futureGames[i];
        //     if (now >= game['startTime']) {
        //         $('#game-start-popup .popup-img img').attr("src", game['imgSrc']);
        //         $('#game-start-popup .popup-title').html(game['title']);
        //         $('#game-start-popup form.popup-play-form').attr("action", "/games/" + game['id']);
        //         $.magnificPopup.open({
        //             type: 'inline',
        //             fixedContentPos: false,
        //             items: {
        //                 src: '#game-start-popup'
        //             }
        //           });
        //     } else {
        //         newFutureGames.push(futureGames[i]);
        //     }
        // }
        // futureGames = newFutureGames;

        // var newOngoingGame = null;
        // if (ongoingGame && now >= game['endTime']) {
        //     $('#game-start-popup .popup-img img').attr("src", game['imgSrc']);
        //     $('#game-start-popup .popup-title').html(game['title']);
        //     $('#game-start-popup form.popup-results-form').attr("action", "/tournaments-results/" + game['id']);
        //     $.magnificPopup.open({
        //         type: 'inline',
        //         fixedContentPos: false,
        //         items: {
        //             src: '#game-start-popup'
        //         }
        //     });
        // } else {
        //     newOngoingGame = ongoingGame;
        // }
        // ongoingGame = newOngoingGame;

        // Run again just after next full second
        setTimeout(tickTock, 1020 - now.getMilliseconds());
    }    

    tickTock()
}
